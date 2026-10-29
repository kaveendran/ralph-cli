"""Show the work graph: a Mermaid diagram (renders on GitHub and in VS Code)
and a plain-text list for the terminal."""

from ralph.graph.schedule import ready_units, waiting_counts
from ralph.model.unit import Status, Unit

STATUS_CLASSES = {
    Status.DONE: "done",
    Status.IN_PROGRESS: "active",
    Status.BLOCKED: "blocked",
    Status.TODO: "todo",
}


def _label(unit: Unit) -> str:
    title = unit.title.replace('"', "'").replace("`", "")
    if len(title) > 40:
        title = title[:39] + "…"
    return f'{unit.id}["{unit.id} · {title}"]'


def mermaid(units: dict[str, Unit]) -> str:
    lines = ["```mermaid", "flowchart TD"]
    for unit in units.values():
        lines.append(f"  {_label(unit)}:::{STATUS_CLASSES[unit.status]}")
    for unit in units.values():
        for dep in unit.depends_on:
            if dep in units:
                lines.append(f"  {dep} --> {unit.id}")
    lines += [
        "  classDef done fill:#d1fadf,stroke:#12b76a,color:#054f31",
        "  classDef active fill:#fef0c7,stroke:#f79009,color:#7a2e0e",
        "  classDef blocked fill:#fee4e2,stroke:#f04438,color:#7a271a",
        "  classDef todo fill:#f2f4f7,stroke:#98a2b3,color:#344054",
        "```",
    ]
    return "\n".join(lines) + "\n"


def text_list(units: dict[str, Unit]) -> str:
    symbols = {Status.DONE: "x", Status.IN_PROGRESS: "~", Status.TODO: " ", Status.BLOCKED: "!"}
    ready = {u.id for u in ready_units(units)}
    waits = waiting_counts(units)
    lines = []
    for unit in units.values():
        tags = " ".join(t for t in (unit.priority, unit.size) if t)
        note = ""
        if unit.id in ready:
            note = "  ← ready"
        elif unit.triage:
            note = "  (triage)"
        elif unit.status is Status.BLOCKED:
            note = f"  — {unit.blocker}" if unit.blocker else ""
        deps = f"  after {', '.join(unit.depends_on)}" if unit.depends_on else ""
        unblocks = f"  unblocks {waits[unit.id]}" if waits.get(unit.id) else ""
        lines.append(f"  [{symbols[unit.status]}] {unit.id:<10} {tags:<6} {unit.title[:60]}{deps}{unblocks}{note}")
    return "\n".join(lines)
