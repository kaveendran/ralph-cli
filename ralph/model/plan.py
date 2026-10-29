"""Read an implementation plan written in markdown.

The parser is tolerant because plans are written by people and by Claude:
- a unit starts at `## Unit: <title> (ID: U037)` and runs to the next `## `
  heading, so its `### Goal`, `### Test Plan`, … subsections stay with it;
- fields look like `**Priority:** P1-High`, anywhere in the unit;
- `**Depends On:** [U033, CR1 U017 (notes, with commas), **U025**]` can span
  several lines; IDs inside parentheses are notes, not dependencies, and
  `CR1 U023/U030` means two units in another CR;
- key files come from `**Key files:**` or the backticked paths in a
  `### Deliverables` subsection;
- the build order comes from an "Implementation Order" section when present.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

UNIT_ID = r"U\d+(?:-[0-9a-f]{4})?"
UNIT_HEADER = re.compile(rf"^## Unit:\s*(?P<title>.+?)\s*\(ID:\s*(?P<id>{UNIT_ID})\)\s*$")
FIELD_LINE = re.compile(r"^(?:[-*]\s+)?\*\*(?P<name>[A-Za-z][A-Za-z ]*?):\*\*\s*(?P<value>.*)$")
UNIT_TOKEN = re.compile(rf"\b{UNIT_ID}\b")
CR_TOKEN = re.compile(r"\bCR(\d+)\b", re.IGNORECASE)
# "§4b" is a plan section, not a spec section, so a trailing letter rules it out.
SECTION_REF = re.compile(r"(?:\bCR(\d+)\s+)?§\s?(\d+(?:\.\d+)*)(?![A-Za-z])")
CRITERION_REF = re.compile(r"\b(?:AC|FR)-\d+\b")
BACKTICKED = re.compile(r"`([^`]+)`")
SIZES = {"small": "S", "medium": "M", "large": "L"}


@dataclass
class PlanUnit:
    id: str
    title: str
    section: str                 # the unit's whole entry, header included
    line: int                    # 1-based line of the header
    priority: str = ""
    size: str = ""
    type: str = ""
    spend: str = ""
    milestone: str = ""
    depends_on: list[str] = field(default_factory=list)
    external_deps: list[str] = field(default_factory=list)
    spec_refs: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass
class Plan:
    path: Path
    units: list[PlanUnit]
    order: list[str]             # unit IDs from the Implementation Order section, if any

    def unit(self, unit_id: str) -> PlanUnit | None:
        return next((u for u in self.units if u.id == unit_id), None)


def _blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Split the plan into level-2 blocks: (start index, lines including the heading)."""
    blocks, start = [], None
    for index, line in enumerate(lines):
        if line.startswith("## "):
            if start is not None:
                blocks.append((start, lines[start:index]))
            start = index
    if start is not None:
        blocks.append((start, lines[start:]))
    return blocks


def _subsection(lines: list[str], title_prefix: str) -> list[str]:
    """The lines of the first `### <title_prefix>…` subsection."""
    inside, found = False, []
    for line in lines:
        if line.startswith("### "):
            if inside:
                break
            inside = line[4:].strip().lower().startswith(title_prefix.lower())
            continue
        if inside:
            found.append(line)
    return found


def blocks(text: str) -> list[tuple[int, str]]:
    """The plan's level-2 blocks as (1-based start line, text including the heading)."""
    return [(start + 1, "\n".join(block)) for start, block in _blocks(text.splitlines())]


def subsection(section: str, title_prefix: str) -> str:
    """The text of a unit's `### <title_prefix>…` subsection, e.g. "Rollback"."""
    return "\n".join(_subsection(section.splitlines(), title_prefix)).strip()


def _fields(lines: list[str]) -> dict[str, str]:
    values = {}
    for line in lines:
        match = FIELD_LINE.match(line.strip())
        if match:
            values.setdefault(match.group("name").strip().lower(), match.group("value").strip())
    return values


def _depends_on_text(lines: list[str]) -> str:
    """The text of the `**Depends On:**` field, following a `[...]` list across lines."""
    for index, line in enumerate(lines):
        position = line.find("**Depends On:**")
        if position < 0:
            continue
        text = line[position + len("**Depends On:**"):].strip()
        if text.startswith("[") and "]" not in text:
            for following in lines[index + 1:]:
                text += " " + following.strip()
                if "]" in following:
                    break
        return text
    return ""


def _strip_parentheses(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\([^()]*\)", "", text)
    return text


def parse_dependencies(text: str) -> tuple[list[str], list[str]]:
    """(same-CR unit IDs, other-CR references like 'cr1:U017') from a Depends On value."""
    same, other = [], []
    text = _strip_parentheses(text).strip().strip("[]")
    for chunk in re.split(r"[,;]", text):
        ids = UNIT_TOKEN.findall(chunk)
        cr = CR_TOKEN.search(chunk)
        for unit_id in ids:
            target, value = (other, f"cr{cr.group(1)}:{unit_id}") if cr else (same, unit_id)
            if value not in target:
                target.append(value)
    return same, other


def parse_spec_refs(text: str, explicit: str = "") -> list[str]:
    """Spec references: `§4.3` (this CR), `cr1:§4.4` (another CR), `AC-012`."""
    source = explicit or text
    refs = []
    for match in SECTION_REF.finditer(source):
        if source[max(0, match.start() - 5):match.start()].lower().endswith("plan "):
            continue  # "plan §10" points into the plan itself
        cr, number = match.groups()
        ref = f"cr{cr}:§{number}" if cr else f"§{number}"
        if ref not in refs:
            refs.append(ref)
    for ref in CRITERION_REF.findall(source):
        if ref not in refs:
            refs.append(ref)
    return refs


def _looks_like_path(token: str) -> bool:
    token = token.strip()
    return " " not in token and ("/" in token or re.search(r"\.[A-Za-z0-9]{1,6}$", token) is not None)


def parse_key_files(lines: list[str], explicit: str = "") -> list[str]:
    if explicit:
        tokens = BACKTICKED.findall(explicit) or [t.strip() for t in explicit.split(",")]
        return [t for t in tokens if t]
    files = []
    for line in _subsection(lines, "Deliverables"):
        match = BACKTICKED.search(line)
        if line.lstrip().startswith(("-", "*")) and match and _looks_like_path(match.group(1)):
            if match.group(1) not in files:
                files.append(match.group(1))
    return files


def parse_unit(header_match: re.Match, lines: list[str], line: int) -> PlanUnit:
    values = _fields(lines)
    priority = re.search(r"P[0-3]", values.get("priority", ""))
    size = next((code for word, code in SIZES.items() if word in values.get("size estimate", "").lower()), "")
    depends_on, external = parse_dependencies(_depends_on_text(lines))
    spend = values.get("spend", "").lower()
    criteria = [l.strip() for l in _subsection(lines, "Acceptance Criteria") if l.strip().startswith(("-", "*"))]
    return PlanUnit(
        id=header_match.group("id"),
        title=header_match.group("title").strip(),
        section="\n".join(lines).strip() + "\n",
        line=line,
        priority=priority.group(0) if priority else "",
        size=size,
        type=(values.get("type", "").split() or [""])[0].strip("`").lower(),
        spend="paid" if "paid" in spend else ("none" if spend else ""),
        milestone=values.get("milestone", ""),
        depends_on=[d for d in depends_on if d != header_match.group("id")],
        external_deps=external,
        spec_refs=parse_spec_refs("\n".join(lines), values.get("spec refs", "")),
        key_files=parse_key_files(lines, values.get("key files", "")),
        acceptance_criteria=criteria,
    )


def parse_plan_text(text: str, path: Path) -> Plan:
    lines = text.splitlines()
    units, order = [], []
    for start, block in _blocks(lines):
        heading = block[0]
        match = UNIT_HEADER.match(heading)
        if match:
            units.append(parse_unit(match, block, start + 1))
        elif "implementation order" in heading.lower() and not order:
            for unit_id in UNIT_TOKEN.findall("\n".join(block[1:])):
                if unit_id not in order:
                    order.append(unit_id)
    return Plan(path=path, units=units, order=order)


def parse_plan(path: Path) -> Plan:
    return parse_plan_text(path.read_text(encoding="utf-8"), path)
