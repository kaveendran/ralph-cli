"""The project's memory as one graph, for a graph store (docs/meta-harness-plan.md §8.3).

The files stay the source of truth: they're in git, reviewable and mergeable.
This export lets a store such as Cognee index them for search across CRs:

  nodes  unit · lesson · change (a ledger entry) · file
  edges  unit depends_on unit · unit plans file · change implements unit ·
         change touched file · lesson learned_in unit · lesson applies_to file

Give the agent the store through its own read-only MCP server in
.ralph/mcp.json (the only MCP servers loop runs load).
"""

from ralph.memory.learnings import read_inbox
from ralph.model.store import UnitStore
from ralph.paths import ProjectPaths
from ralph.team.ledger import read_local
from ralph.vcs.git import Git


def build(project: ProjectPaths) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(node_id: str, kind: str, **props) -> str:
        nodes.setdefault(node_id, {"id": node_id, "kind": kind, **props})
        return node_id

    def edge(source: str, relation: str, target: str) -> None:
        edges.append({"from": source, "rel": relation, "to": target})

    for cr_name in project.cr_names():
        store = UnitStore(project.cr(cr_name).units_dir)
        if not store.exists():
            continue
        for unit in store.load().values():
            uid = node(f"unit:{cr_name}/{unit.id}", "unit", cr=cr_name, title=unit.title, status=unit.status.value,
                       type=unit.type, handoff=unit.handoff.strip())
            for dep in unit.depends_on:
                edge(uid, "depends_on", f"unit:{cr_name}/{dep}")
            for path in unit.key_files:
                edge(uid, "plans", node(f"file:{path}", "file", path=path))
    if Git(project.root).is_repo():
        for entry in read_local(Git(project.root)):
            cid = node(f"change:{entry.id}", "change", dev=entry.dev, at=entry.at, summary=entry.summary,
                       commit=entry.commit, breaking=[i["name"] for i in entry.breaking])
            edge(cid, "implements", f"unit:{entry.cr}/{entry.unit}")
            for path in entry.paths:
                edge(cid, "touched", node(f"file:{path}", "file", path=path))
    for lesson in read_inbox(project.learnings_inbox):
        lid = node(f"lesson:{lesson.key}", "lesson", rule=lesson.rule, trigger=lesson.trigger, status=lesson.status)
        if lesson.unit:
            edge(lid, "learned_in", f"unit:{lesson.cr}/{lesson.unit}")
        for pattern in lesson.paths:
            edge(lid, "applies_to", node(f"file:{pattern}", "file", path=pattern))
    known = set(nodes)
    return {"nodes": list(nodes.values()), "edges": [e for e in edges if e["from"] in known and e["to"] in known]}
