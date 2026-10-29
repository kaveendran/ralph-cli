"""One JSON line per iteration in `log/iterations.jsonl`.

These records answer "is the loop staying fast?": per unit, how many turns,
minutes and dollars it took, how large the context got, and whether it
compacted. `ralph status` and `ralph watch` read them.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class IterationRecord:
    run_id: str
    iteration: int
    cr: str
    unit: str
    attempt: int
    outcome: str                  # done | in_progress | blocked | capped | failed | usage_limit | ...
    developer: str = ""
    model: str = ""
    started_at: str = ""
    head_before: str = ""
    head_after: str = ""
    agent: dict = field(default_factory=dict)        # StreamStats.summary()
    gates: list[dict] = field(default_factory=list)  # name, passed, exit_code, seconds
    review: str = ""
    review_cost_usd: float = 0.0
    integrity_failures: list[str] = field(default_factory=list)
    changed_files: int = 0
    commit: str = ""
    run_dir: str = ""
    notes: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_record(path: Path, record: IterationRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as log:
        log.write(json.dumps(asdict(record), separators=(",", ":"), ensure_ascii=False) + "\n")


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def unit_totals(records: list[dict], cr: str) -> dict[str, dict]:
    """Per unit: iterations, turns, minutes, dollars and peak context so far."""
    totals: dict[str, dict] = {}
    for record in records:
        if record.get("cr") != cr:
            continue
        agent = record.get("agent") or {}
        unit = totals.setdefault(record.get("unit", "?"), {
            "iterations": 0, "turns": 0, "minutes": 0.0, "cost_usd": 0.0, "peak_context": 0, "compactions": 0})
        unit["iterations"] += 1
        unit["turns"] += agent.get("num_turns") or 0
        unit["minutes"] += (agent.get("duration_ms") or 0) / 60000
        unit["cost_usd"] += (agent.get("total_cost_usd") or 0) + (record.get("review_cost_usd") or 0)
        unit["peak_context"] = max(unit["peak_context"], agent.get("peak_context") or 0)
        unit["compactions"] += agent.get("compactions") or 0
    return totals
