"""Evaluate a skill: does Claude pick it when it should, and is the answer right?

Following "build evaluations first" from Anthropic's skill best practices,
each skill can have scenarios in `.claude/skills/<name>/evals/*.json`:

    {"prompt": "Add an endpoint that lists invoices.",
     "should_trigger": true,
     "expect": ["Pydantic", "router"]}

Each scenario is one real Claude call with read-only tools plus the Skill
tool, in this project, so it costs a little. It runs only on demand.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ralph.runner.analysis import READ_ONLY, analysis_call
from ralph.runner.base import Runner
from ralph.skills.registry import Skill


@dataclass
class EvalResult:
    skill: str
    scenario: str
    triggered: bool
    should_trigger: bool
    missing: list[str]
    cost_usd: float

    @property
    def passed(self) -> bool:
        return self.triggered == self.should_trigger and not self.missing


def scenarios(skill: Skill) -> list[tuple[str, dict]]:
    found = []
    for path in sorted((skill.dir / "evals").glob("*.json")):
        try:
            found.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return found


def run_evals(runner: Runner, skill: Skill, root: Path, run_dir: Path, model: str = "haiku") -> list[EvalResult]:
    results = []
    for name, scenario in scenarios(skill):
        call = analysis_call(root, scenario["prompt"], run_dir / f"{skill.name}-{name}", model=model,
                             tools=["Skill", *READ_ONLY], max_turns=8, max_budget_usd=0.5)
        result = runner.run(call)
        answer = (result.result.get("result") or result.stats.last_text or "").lower()
        missing = [e for e in scenario.get("expect", []) if e.lower() not in answer]
        results.append(EvalResult(skill.name, name, skill.name in result.stats.skills_invoked,
                                  bool(scenario.get("should_trigger", True)), missing,
                                  float(result.result.get("total_cost_usd") or 0)))
    return results
