"""Score how complex each unit is, so oversized units are split before the loop
runs them (the idea comes from Taskmaster's complexity analysis).

One read-only Claude call scores every unit from 1 to 5 and proposes splits
for the 4s and 5s. It complements the deterministic size check, which only
counts key files.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ralph.model.plan import Plan
from ralph.model.unit import Unit
from ralph.prompts import render
from ralph.runner.analysis import analysis_call
from ralph.runner.base import Runner

SECTION_CHARS = 1_500     # of each unit's plan entry, so the whole plan fits one call


@dataclass
class Score:
    unit_id: str
    score: int
    reason: str
    split: list[str] = field(default_factory=list)

    @property
    def too_complex(self) -> bool:
        return self.score >= 4


def describe(plan: Plan, units: dict[str, Unit]) -> str:
    parts = []
    for unit in units.values():
        plan_unit = plan.unit(unit.id)
        entry = (plan_unit.section if plan_unit else unit.description)[:SECTION_CHARS]
        parts.append(f"### {unit.id} — {unit.title}\nKey files: {len(unit.key_files)} · "
                     f"acceptance criteria: {len(plan_unit.acceptance_criteria) if plan_unit else '?'}\n\n{entry}")
    return "\n\n".join(parts)


def analyze(runner: Runner, root: Path, plan: Plan, units: dict[str, Unit], run_dir: Path,
            model: str = "sonnet") -> list[Score]:
    prompt = render("complexity.md", UNITS=describe(plan, units))
    result = runner.run(analysis_call(root, prompt, run_dir, schema_name="complexity.json", model=model,
                                      max_turns=3, max_budget_usd=3.0))
    scores = []
    for item in (result.structured or {}).get("units", []):
        if item.get("id") in units:
            scores.append(Score(item["id"], int(item["score"]), item.get("reason", ""), list(item.get("split", []))))
    return scores
