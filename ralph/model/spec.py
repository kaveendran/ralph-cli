"""Find the parts of a spec that a unit cites.

Spec sections are numbered headings: `## 4. The surface contract`,
`### 4.3 Refusals`. A reference like `§4.3` resolves to that section's text
(its own subsections included), and `AC-012` resolves to the lines that
mention it. Excerpts are capped so a unit that cites a whole chapter doesn't
flood the context; anything over the cap becomes a pointer to read on demand.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

NUMBERED_HEADING = re.compile(r"^(#{1,6})\s+§?\s?(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
ANY_HEADING = re.compile(r"^(#{1,6})\s")


@dataclass
class SpecSection:
    number: str
    title: str
    level: int
    start: int                   # 1-based line of the heading
    end: int                     # 1-based last line
    text: str


@dataclass
class Spec:
    path: Path
    lines: list[str]
    sections: dict[str, SpecSection] = field(default_factory=dict)

    def section(self, number: str) -> SpecSection | None:
        return self.sections.get(number)

    def lines_mentioning(self, token: str, context: int = 1) -> str:
        """The lines that mention `token` (plus `context` lines after each)."""
        found = []
        for index, line in enumerate(self.lines):
            if re.search(rf"\b{re.escape(token)}\b", line):
                found.append("\n".join(self.lines[index:index + 1 + context]))
        return "\n".join(found)


def parse_spec_text(text: str, path: Path) -> Spec:
    lines = text.splitlines()
    headings = []  # (index, level, number or None, title)
    for index, line in enumerate(lines):
        numbered = NUMBERED_HEADING.match(line)
        if numbered:
            headings.append((index, len(numbered.group(1)), numbered.group(2), numbered.group(3)))
        elif ANY_HEADING.match(line):
            headings.append((index, len(ANY_HEADING.match(line).group(1)), None, line))
    spec = Spec(path=path, lines=lines)
    for position, (index, level, number, title) in enumerate(headings):
        if number is None or number in spec.sections:
            continue
        end = len(lines)
        for later_index, later_level, _, _ in headings[position + 1:]:
            if later_level <= level:
                end = later_index
                break
        spec.sections[number] = SpecSection(number, title, level, index + 1, end,
                                            "\n".join(lines[index:end]).strip())
    return spec


def parse_spec(path: Path) -> Spec | None:
    if not path.exists():
        return None
    return parse_spec_text(path.read_text(encoding="utf-8"), path)


def acceptance_criteria(spec: Spec | None) -> str:
    """The spec's acceptance criteria: its "acceptance criteria" section, else lines with AC- IDs."""
    if spec is None:
        return ""
    for section in spec.sections.values():
        if "acceptance criteria" in section.title.lower():
            return section.text
    return spec.lines_mentioning("AC", context=0) if any("AC-" in line for line in spec.lines) else ""


@dataclass
class Excerpts:
    text: str
    included: list[str]          # refs whose text is in `text`
    pointers: list[str]          # refs left for the agent to open, with line ranges


def excerpts(refs: list[str], specs: dict[str, Spec | None], own_cr: str,
             max_chars: int = 12_000, max_section_chars: int = 5_000) -> Excerpts:
    """The cited spec text for a unit.

    `specs` maps CR names to their parsed spec; `own_cr` is the unit's CR, used
    for refs without a CR prefix. A section longer than `max_section_chars`,
    or one that doesn't fit in `max_chars`, becomes a pointer instead.
    """
    parts, included, pointers, used = [], [], [], 0
    for ref in refs:
        cr, _, token = ref.rpartition(":")
        cr = cr or own_cr
        spec = specs.get(cr)
        label = ref if cr == own_cr else f"{cr} {token}"
        if spec is None:
            pointers.append(f"{label} (no spec found for {cr})")
            continue
        if token.startswith("§"):
            section = spec.section(token[1:])
            if section is None:
                pointers.append(f"{label} (no such numbered section in {spec.path.name})")
                continue
            body = f"### {label} — {section.title}\n\n{section.text}"
            where = f"{spec.path} lines {section.start}–{section.end}"
        else:
            body = spec.lines_mentioning(token)
            if not body:
                pointers.append(f"{label} (not found in {spec.path.name})")
                continue
            body = f"### {label}\n\n{body}"
            where = f"search {spec.path} for {token}"
        if len(body) > max_section_chars or used + len(body) > max_chars:
            pointers.append(f"{label}: too long to include; read {where}")
            continue
        parts.append(body)
        included.append(label)
        used += len(body)
    return Excerpts("\n\n".join(parts), included, pointers)
