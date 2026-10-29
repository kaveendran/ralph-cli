"""The bounded critique → edit loop, with you deciding every edit.

Each round: critique the document (or reuse a report newer than it), stop
if nothing CRITICAL or IMPORTANT is left, have Claude propose a revision,
show the diff, and ask you to apply, skip, finish or quit. It's deliberately
not autonomous: a model editing a model's plan drifts when nobody checks.
Every version is kept in `revisions/`.
"""

import difflib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ralph import ui
from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.workflows.report import severities

DIFF_LINES = 120
ACTIONS = ["apply — accept this revision and critique again", "skip — reject it, try another round",
           "done — accept it and stop", "quit — reject it and stop"]


@dataclass
class Refinement:
    target: Path
    revisions_dir: Path
    critique: Callable[[], Path]                 # runs a critique; returns the report's path
    latest_report: Callable[[], Path | None]
    propose: Callable[[str], str]                # report text → the revised document (RalphError if unusable)
    max_iterations: int = 3
    gate: str = "human"                          # human | auto (auto: apply without asking; for CI)


def next_version(revisions_dir: Path) -> int:
    numbers = [int(m.group(1)) for p in revisions_dir.glob("v*.md") if (m := re.match(r"v(\d+)", p.name))]
    return max(numbers, default=0) + 1


def show_diff(before: str, after: str, name: str) -> None:
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"{name} (now)",
                                     f"{name} (proposed)", lineterm=""))
    for line in diff[:DIFF_LINES]:
        ui.info(f"    {line}")
    if len(diff) > DIFF_LINES:
        ui.info(f"    … {len(diff) - DIFF_LINES} more diff lines")


def refine(job: Refinement) -> int:
    job.revisions_dir.mkdir(parents=True, exist_ok=True)
    initial = job.revisions_dir / "v0-initial.md"
    if not initial.exists():
        shutil.copyfile(job.target, initial)

    for iteration in range(1, job.max_iterations + 1):
        ui.section(f"Round {iteration} of {job.max_iterations}")
        existing = job.latest_report()
        if existing and existing.stat().st_mtime >= job.target.stat().st_mtime:
            report = existing          # the document hasn't changed since (first round, or after a skip)
            ui.ok(f"Using the critique from {existing.name} (newer than the document).")
        else:
            ui.info("  Critiquing…")
            report = job.critique()
        counts = severities(read_text(report))
        ui.info(f"  CRITICAL {counts['CRITICAL']} · IMPORTANT {counts['IMPORTANT']} · SUGGESTED {counts['SUGGESTED']}")
        if counts["CRITICAL"] == 0 and counts["IMPORTANT"] == 0:
            ui.ok("Nothing CRITICAL or IMPORTANT is left.")
            break

        ui.info("  Proposing a revision…")
        current = read_text(job.target)
        try:
            candidate = job.propose(read_text(report))
        except RalphError as problem:
            ui.warn(f"The proposed revision can't be used: {problem.message}")
            break
        if candidate.strip() == current.strip():
            ui.ok("The revision changes nothing, so the loop stops here.")
            break
        version = next_version(job.revisions_dir)
        candidate_path = job.revisions_dir / f"v{version}-candidate.md"
        write_text_atomic(candidate_path, candidate)
        show_diff(current, candidate, job.target.name)

        action = "apply" if job.gate == "auto" else ui.choose("Apply this revision?", ACTIONS).split()[0]
        if action in ("apply", "done"):
            write_text_atomic(job.revisions_dir / f"v{version}.md", candidate)
            write_text_atomic(job.target, candidate)
            ui.ok(f"Applied revision v{version} to {job.target.name}.")
            if action == "done":
                break
        elif action == "skip":
            ui.info(f"  Skipped; the candidate stays at {candidate_path}.")
        else:
            ui.info(f"  Stopped; the candidate stays at {candidate_path}.")
            break
    ui.info(f"\n  Every version is in {job.revisions_dir}.")
    return 0
