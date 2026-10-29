"""Decide what an iteration's evidence means for its unit.

A pure function, so the rules are easy to read and to test:
- infrastructure trouble (Docker down, a port in use) → stop the loop;
  it isn't the unit's fault and doesn't count as an attempt;
- the agent reports `blocked` → the unit is blocked;
- `done`, and the integrity check, the gates and the review all agree → done;
- anything else is another attempt; after `max_attempts` the unit is
  blocked for a human, and so is a review that raises the same gaps twice.
"""

from dataclasses import dataclass, field
from enum import Enum

from ralph.verify.gates import GateResult
from ralph.verify.integrity import IntegrityReport
from ralph.verify.review import Review


class Verdict(Enum):
    DONE = "done"
    RETRY = "retry"
    BLOCKED = "blocked"
    INFRA = "infra"


@dataclass
class Evidence:
    reported: str | None                   # the agent's status: done | partial | blocked | None (no valid result)
    blocker: str = ""
    capped: bool = False                   # stopped by the turn, budget or time cap
    stopped_by: str = ""                   # "looping", "silence", … when the harness stopped the run
    integrity: IntegrityReport | None = None
    gates: list[GateResult] = field(default_factory=list)
    review: Review | None = None
    previous_review_gaps: list[str] = field(default_factory=list)


@dataclass
class Judgement:
    verdict: Verdict
    reason: str
    counts_attempt: bool


def _why_not_done(e: Evidence) -> str:
    if e.stopped_by:
        return f"the run was stopped ({e.stopped_by})"
    if e.capped:
        return "the run hit its turn, budget or time cap"
    if e.reported is None:
        return "the agent didn't return a valid structured result"
    if e.reported == "partial":
        return "the agent reported the unit as partial"
    if e.integrity and not e.integrity.passed:
        return "integrity check failed: " + "; ".join(e.integrity.failures)
    failed = next((g for g in e.gates if not g.passed), None)
    if failed:
        return f"gate `{failed.name}` failed (exit {failed.exit_code})"
    if e.review and e.review.verdict == "changes_requested":
        return "review asked for changes: " + "; ".join(e.review.gaps + e.review.rule_violations)[:500]
    return "the unit isn't done"


def judge(e: Evidence, attempts_so_far: int, max_attempts: int) -> Judgement:
    if any(g.infra_error for g in e.gates):
        failed = next(g for g in e.gates if g.infra_error)
        return Judgement(Verdict.INFRA, f"gate `{failed.name}` hit an infrastructure problem "
                         f"(exit {failed.exit_code}); fix it and run again", counts_attempt=False)
    if e.reported == "blocked":
        return Judgement(Verdict.BLOCKED, e.blocker or "the agent reported the unit as blocked", True)

    review_ok = e.review is None or e.review.verdict in ("approve", "unavailable")
    if (e.reported == "done" and not e.capped and not e.stopped_by
            and (e.integrity is None or e.integrity.passed)
            and all(g.passed for g in e.gates) and review_ok):
        return Judgement(Verdict.DONE, "done", counts_attempt=True)

    reason = _why_not_done(e)
    if e.review and e.review.gaps and sorted(e.review.gaps) == sorted(e.previous_review_gaps):
        return Judgement(Verdict.BLOCKED, "review stalemate: the same gaps were raised twice — " + reason, True)
    if attempts_so_far + 1 >= max_attempts:
        return Judgement(Verdict.BLOCKED, f"gave up after {attempts_so_far + 1} attempts: {reason}", True)
    return Judgement(Verdict.RETRY, reason, counts_attempt=True)
