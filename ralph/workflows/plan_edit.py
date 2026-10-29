"""Apply structured edits to a plan, so a refinement can't lose or mangle units.

The old refine-plan asked Claude to reprint the whole plan, which for a
278 KB plan is slow, expensive and can come back truncated. Instead Claude
returns only what changes: replaced unit entries, replaced other sections
(by heading), new units and open questions. The harness splices them in,
so every unit it didn't touch stays byte-for-byte the same, and it rejects
an edit that would rename, renumber or drop a unit.
"""

import re

from ralph.errors import RalphError
from ralph.model.plan import UNIT_HEADER, blocks, parse_plan_text

OPEN_QUESTIONS = "## Open Questions"


def _header(section: str) -> re.Match | None:
    first = section.strip().splitlines()[0] if section.strip() else ""
    return UNIT_HEADER.match(first)


def _ends_with_blank_line(block: str) -> str:
    """So the block that follows starts after a blank line (blocks are joined with one newline)."""
    return block.rstrip("\n") + "\n"


def apply_edits(text: str, edits: dict) -> str:
    """The plan with `edits` applied (the shape of schemas/plan-edits.json).

    Untouched blocks keep their exact text and separators: the plan is
    rebuilt as `lead + "\\n".join(blocks)`, which is how it was split."""
    found = blocks(text)
    if not found:
        raise RalphError("The plan has no `## ` sections to edit.")
    lines = text.splitlines()
    lead = "".join(line + "\n" for line in lines[:found[0][0] - 1])
    parts = [block for _, block in found]
    by_unit = {m.group("id"): i for i, b in enumerate(parts) if (m := _header(b))}
    by_heading = {b.splitlines()[0].strip(): i for i, b in enumerate(parts)}

    for edit in edits.get("units", []):
        unit_id, section = edit.get("unit", ""), edit.get("section", "").strip()
        if unit_id not in by_unit:
            raise RalphError(f"The revision edits {unit_id}, which isn't in the plan.")
        header, old = _header(section), _header(parts[by_unit[unit_id]])
        if header is None or header.group("id") != unit_id:
            raise RalphError(f"The revised entry for {unit_id} doesn't start with its `## Unit: … (ID: {unit_id})` header.")
        if header.group("title").strip() != old.group("title").strip() and "deprecated" not in section.lower():
            raise RalphError(f"The revision renames {unit_id}; unit names stay (mark a unit deprecated instead).")
        parts[by_unit[unit_id]] = section + "\n"

    for edit in edits.get("sections", []):
        heading, body = edit.get("heading", "").strip(), edit.get("text", "").strip()
        if heading not in by_heading:
            raise RalphError(f"The revision edits the section '{heading}', which isn't in the plan.")
        if _header(heading):
            raise RalphError(f"Unit entries are edited under `units`, not `sections` ({heading}).")
        replacement = body if body.startswith(heading) else f"{heading}\n\n{body}"
        parts[by_heading[heading]] = replacement + "\n"

    new_units = []
    for edit in edits.get("new_units", []):
        section = edit.get("section", "").strip()
        header = _header(section)
        if header is None:
            raise RalphError("A new unit doesn't start with a `## Unit: Name (ID: Unnn)` header.")
        if header.group("id") in by_unit or any(_header(s).group("id") == header.group("id") for s in new_units):
            raise RalphError(f"The new unit {header.group('id')} reuses an ID; new units take the next free one.")
        new_units.append(section + "\n")
    if new_units:
        last_unit = max(by_unit.values(), default=len(parts) - 1)
        parts[last_unit] = _ends_with_blank_line(parts[last_unit])
        parts[last_unit + 1:last_unit + 1] = new_units

    questions = [q.strip() for q in edits.get("open_questions", []) if q.strip()]
    if questions:
        lines = "".join(f"- {q}\n" for q in questions)
        index = next((i for i, b in enumerate(parts) if b.splitlines()[0].strip().lower() == OPEN_QUESTIONS.lower()), None)
        if index is None:
            parts[-1] = _ends_with_blank_line(parts[-1])
            parts.append(f"{OPEN_QUESTIONS}\n\n{lines}")
        else:
            parts[index] = parts[index].rstrip("\n") + "\n" + lines

    revised = lead + "\n".join(parts)
    revised = revised.rstrip("\n") + "\n" if revised != text.rstrip("\n") else text
    before = {u.id for u in parse_plan_text(text, None).units}
    after = {u.id for u in parse_plan_text(revised, None).units}
    if before - after:
        raise RalphError(f"The revision would lose {', '.join(sorted(before - after))}.")
    return revised
