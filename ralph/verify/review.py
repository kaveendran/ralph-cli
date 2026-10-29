"""The per-unit review: a separate, read-only Claude session checks that the
unit's tests prove its acceptance criteria.

It is an evaluator, not the author: a fresh context, read-only tools, and the
acceptance criteria as its yardstick (the evaluator-optimizer pattern from
Anthropic's "Building effective agents"). The kernel builds the call, so the
review runs as hermetically as the build itself.
"""

from dataclasses import dataclass, field

from ralph.prompts import render
from ralph.runner.base import AgentCall, Runner

MAX_DIFF_CHARS = 40_000
REVIEW_TOOLS = ["Read", "Grep", "Glob"]


@dataclass
class Review:
    verdict: str                  # "approve", "changes_requested" or "unavailable"
    gaps: list[str] = field(default_factory=list)
    rule_violations: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def approved(self) -> bool:
        return self.verdict == "approve"

    def as_notes(self) -> str:
        lines = [f"- {g}" for g in self.gaps] + [f"- rule: {v}" for v in self.rule_violations]
        return "\n".join(lines)


def should_review(mode: str, priority: str, size: str) -> bool:
    if mode == "all":
        return True
    if mode == "p0-and-large":
        return priority == "P0" or size == "L"
    return False


def review_prompt(unit_section: str, rules: str, changed_files: list[str], diff: str,
                  integrity_flags: list[str]) -> str:
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n[… diff cut to fit; read the files for the rest]"
    flags = ("Flagged by the harness: " + "; ".join(integrity_flags)) if integrity_flags else ""
    return render("review.md", UNIT_SECTION=unit_section, RULES=rules.strip() or "(none)",
                  CHANGED_FILES=", ".join(changed_files) or "(none)", INTEGRITY_FLAGS=flags, DIFF=diff)


def run_review(runner: Runner, call: AgentCall) -> Review:
    """Run a review call prepared by the kernel (read-only tools, review schema)."""
    result = runner.run(call)
    data = result.structured
    cost = float(result.result.get("total_cost_usd") or 0)
    if not data:
        return Review("unavailable", cost_usd=cost)
    return Review(data.get("verdict", "unavailable"), list(data.get("gaps", [])),
                  list(data.get("rule_violations", [])), cost)
