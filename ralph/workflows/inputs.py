"""What the workflow experts read, kept bounded however large the project grows.

The old workflows pasted whole files into every prompt: a 278 KB plan went
to each of eight experts, and the retro read 850 KB of progress diaries.
Here a long plan becomes an outline (every unit's header and opening lines,
plus a pointer to its line in the file), and the experts get read-only tools
to open the parts they need. The retro reads unit state, metrics, the heads
of notes and the git log instead of the diaries.
"""

import re
from pathlib import Path

from ralph.fsutil import read_text
from ralph.model.plan import Plan, blocks, subsection
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.vcs.git import Git

PLAN_CHARS = 60_000
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|e2e)/|(_test|\.test|\.spec|_spec)\.[a-z]+$|(^|/)test_[^/]+$")


def _cut(text: str, limit: int, where: str) -> str:
    if len(text) <= limit:
        return text.rstrip()
    return text[:limit].rstrip() + f"\n[… cut; the rest is in {where}]"


def plan_text(plan: Plan, limit: int = PLAN_CHARS) -> str:
    """The whole plan when it fits; otherwise an outline with pointers into the file."""
    text = read_text(plan.path)
    if len(text) <= limit:
        return text
    parts = [f"(The plan is {len(text):,} characters, so this is an outline: each section's opening lines. "
             f"Open {plan.path} with Read at the line given when you need the rest.)"]
    sections = blocks(text)
    per_section = max(500, (limit - 2_000) // max(len(sections), 1))
    for line, block in sections:
        keep = per_section * 3 if "implementation order" in block.splitlines()[0].lower() else per_section
        parts.append(_cut(block, keep, f"{plan.path.name} from line {line}"))
    return _cut("\n\n".join(parts), limit, str(plan.path))


def unit_table(units: dict[str, Unit], totals: dict[str, dict] | None = None) -> str:
    """One row per unit: state, attempts, where it came from, and what it cost."""
    totals = totals or {}
    rows = ["| Unit | Title | Type | Status | Attempts | Done | From | Iterations | Minutes | $ |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for unit in sorted(units.values(), key=lambda u: (u.order or 10_000, u.id)):
        spent = totals.get(unit.id, {})
        origin = f"found in {unit.discovered_from}" if unit.discovered_from else ("triage" if unit.triage else "plan")
        rows.append(f"| {unit.id} | {unit.title[:60]} | {unit.type or '-'} | {unit.status.value} | {unit.attempts} | "
                    f"{unit.done.get('at', '')[:10]} | {origin} | {spent.get('iterations', 0)} | "
                    f"{spent.get('minutes', 0):.0f} | {spent.get('cost_usd', 0):.2f} |")
    return "\n".join(rows)


def git_log(git: Git, limit: int = 300) -> str:
    if not git.is_repo():
        return "(no git history)"
    return git.run("log", f"-{limit}", "--date=short", "--format=%h %ad %an: %s", check=False).stdout.strip() \
        or "(no commits)"


def notes_head(store: UnitStore, unit: Unit, lines: int = 12) -> str:
    text = read_text(store.notes_path(unit.id)).strip()
    if not text:
        return ""
    kept = text.splitlines()[:lines]
    return "\n".join(kept) + ("\n[…]" if len(text.splitlines()) > lines else "")


def trouble(units: dict[str, Unit], store: UnitStore, cr: str, limit: int = 12_000) -> str:
    """Blocked units, units that needed retries, and review gaps, with the start of their notes."""
    parts = []
    for unit in sorted(units.values(), key=lambda u: u.id):
        if unit.status is not Status.BLOCKED and unit.attempts <= 1 and not unit.last_review_gaps:
            continue
        lines = [f"### {cr}:{unit.id} {unit.title} — {unit.status.value}, {unit.attempts} attempt(s)"]
        if unit.blocker:
            lines.append(f"Blocker: {unit.blocker}")
        if unit.last_review_gaps:
            lines.append("Review gaps: " + "; ".join(unit.last_review_gaps[:4]))
        head = notes_head(store, unit)
        if head:
            lines.append(head)
        parts.append("\n".join(lines))
    return _cut("\n\n".join(parts), limit, f"development/{cr}/units/*.notes.md") or "(none)"


def legacy_trouble(progress: Path, limit: int = 6_000) -> str:
    """For a CR from the previous loop: only the progress lines about blockers."""
    words = re.compile(r"\[!\]|blocked|stuck|waiting on", re.I)
    lines = [line.strip() for line in read_text(progress).splitlines() if words.search(line)]
    return _cut("\n".join(lines[:120]), limit, str(progress)) or "(none)"


def rollback_sections(plan: Plan, per_unit: int = 1_500) -> str:
    """Each unit's `### Rollback…` subsection, verbatim, or a note that it has none."""
    parts = []
    for unit in plan.units:
        text = subsection(unit.section, "Rollback")
        parts.append(f"### {unit.id} — {unit.title}\n" +
                     (_cut(text, per_unit, f"{plan.path.name} line {unit.line}") if text else "(no Rollback Plan section)"))
    return "\n\n".join(parts) or "(the plan has no units)"


def unit_tests(git: Git, units: dict[str, Unit], ledger_files: dict[str, list[str]]) -> str:
    """Which test files each finished unit changed: from its ledger entry, else from its commit."""
    rows = []
    for unit in sorted(units.values(), key=lambda u: u.id):
        if unit.status is not Status.DONE:
            continue
        files = ledger_files.get(unit.id)
        if files is None:
            sha = git.run("log", "-1", "--format=%H", "--fixed-strings", f"--grep={unit.id}: {unit.title}",
                          check=False).stdout.strip()
            files = git.run("show", "--name-only", "--format=", sha, check=False).stdout.split() if sha else []
        tests = [f for f in files if TEST_FILE.search(f)]
        rows.append(f"- {unit.id} {unit.title[:50]}: " + (", ".join(tests[:8]) if tests else "no test files changed"))
    return "\n".join(rows) or "(no finished units)"
