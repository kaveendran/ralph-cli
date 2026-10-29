"""`ralph start --lanes N`: build independent units side by side (docs/meta-harness-plan.md §6.3).

Each round:
  1. pick up to N ready units whose key files don't overlap (a unit with no key
     files runs alone: nothing shows it's independent);
  2. give each its own git worktree and branch, under .git/ralph-lanes/, and run
     `ralph start --unit <ID> --max 1` there, so each lane is an ordinary,
     fully verified iteration;
  3. merge the finished lanes into your branch one at a time, in build order:
     a conflict check, a cherry-pick, then the unit's gates on the combined code.
     A lane that conflicts or fails there is saved under refs/ralph/rejected/
     and its unit runs again normally;
  4. carry each lane's logs and iteration records back, and remove its worktree.
When fewer than two units can run in parallel, the ordinary loop takes over,
including the convergence check at the end. Nothing is pushed.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ralph import ui
from ralph.fsutil import read_text
from ralph.graph.schedule import ready_units
from ralph.kernel.lock import RunLock
from ralph.kernel.loop import Kernel
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.prompts import PACKAGE_DIR
from ralph.team.claims import others_claims, read_claims
from ralph.team.impact import touches
from ralph.vcs import commit as vcs
from ralph.vcs.git import Git
from ralph.verify import gates as gate_runner

MAX_LANES = 4


@dataclass
class Lane:
    unit: Unit
    path: Path
    branch: str
    log: Path
    process: subprocess.Popen | None = None

    @property
    def finished(self) -> bool:
        return self.process is not None and self.process.poll() is not None


def overlaps(a: Unit, b: Unit) -> bool:
    return any(touches(x, b.key_files) or touches(y, a.key_files) for x in a.key_files for y in b.key_files)


def pick(units: dict[str, Unit], lanes: int, exclude: set[str] = frozenset()) -> list[Unit]:
    """Ready units that can run side by side: no shared key files, and no unfinished work
    in your tree (a lane starts from the last commit, so it wouldn't see that work)."""
    chosen: list[Unit] = []
    for unit in ready_units(units):
        if unit.id in exclude or not unit.key_files or unit.wip_paths:
            continue
        if all(not overlaps(unit, other) for other in chosen):
            chosen.append(unit)
        if len(chosen) == lanes:
            break
    return chosen


class LaneRunner:
    def __init__(self, kernel: Kernel, lanes: int, child_args: list[str]):
        self.kernel, self.lanes, self.child_args = kernel, min(max(lanes, 1), MAX_LANES), child_args
        self.git = kernel.git
        common = Path(self.git.out("rev-parse", "--git-common-dir"))
        self.home = (common if common.is_absolute() else kernel.root / common) / "ralph-lanes"

    def run(self) -> int:
        kernel = self.kernel
        with RunLock(kernel.project.lock):
            kernel.preflight()
            ui.info(f"  Lanes: up to {self.lanes} units at once, so spending can run up to {self.lanes}× as fast.")
            rounds = 0
            while True:
                units = kernel.store.load()
                theirs = set(others_claims(read_claims(self.git), kernel.email, kernel.cr.name))
                batch = pick(units, self.lanes, theirs | self._only_excluded(units))
                if len(batch) < 2:
                    break
                rounds += 1
                self.round(rounds, batch)
                if kernel.over_budget():
                    return 0
        ui.info("\n  Nothing more can run side by side; continuing one unit at a time.")
        kernel.options.skip_baseline = True
        return kernel.run()

    def _only_excluded(self, units: dict[str, Unit]) -> set[str]:
        only = set(self.kernel.options.only_units)
        return {u for u in units if only and u not in only}

    # ── One round ────────────────────────────────────────────────────────────

    def round(self, number: int, batch: list[Unit]) -> None:
        base = self.git.head()
        ui.section(f"Lanes round {number}: {', '.join(u.id for u in batch)} (from {base[:10]})")
        lanes = [self.start(unit, base) for unit in batch]
        try:
            while not all(lane.finished for lane in lanes):
                time.sleep(1)
        except KeyboardInterrupt:
            for lane in lanes:
                if lane.process and lane.process.poll() is None:
                    lane.process.wait()        # each lane got the Ctrl-C too, and keeps its work
            ui.warn(f"Stopped. The lanes' worktrees stay in {self.home} for you to inspect "
                    "('git worktree list'; remove them with 'git worktree remove <path>').")
            raise
        for lane in lanes:                     # in build order, which is the order they were picked
            self.collect(lane)
            self.merge(lane, base)
            self.remove(lane)

    def start(self, unit: Unit, base: str) -> Lane:
        path, branch = self.home / unit.id, f"ralph/lane/{unit.id}"
        if path.exists():
            self.git.run("worktree", "remove", "--force", str(path), check=False)
        self.git.run("branch", "-D", branch, check=False)
        self.git.run("worktree", "add", "-q", "-b", branch, str(path), base)
        log = self.kernel.run_dir / "lanes" / f"{unit.id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-P", "-m", "ralph", "start", "--cr", self.kernel.cr.name, "--unit", unit.id,
                   "--max", "1", "--skip-baseline", "--allow-main", *self.child_args]
        env = {**os.environ, "PYTHONPATH": str(PACKAGE_DIR.parent)}
        with open(log, "w", encoding="utf-8") as handle:
            process = subprocess.Popen(command, cwd=path, env=env, stdin=subprocess.DEVNULL, stdout=handle,
                                       stderr=subprocess.STDOUT)
        ui.info(f"  → {unit.id} {unit.title} in {path}")
        return Lane(unit, path, branch, log, process)

    def collect(self, lane: Lane) -> None:
        """Bring the lane's iteration records and run folders into the main log."""
        lane_log = lane.path / "log"
        records = read_text(lane_log / "iterations.jsonl")
        if records:
            with open(self.kernel.project.iterations_log, "a", encoding="utf-8") as handle:
                handle.write(records)
            for line in records.splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                agent = record.get("agent") or {}
                self.kernel.spent_usd += (agent.get("total_cost_usd") or 0) + (record.get("review_cost_usd") or 0)
        for run in lane_log.glob("2*"):
            if run.is_dir():
                shutil.copytree(run, self.kernel.run_dir / "lanes" / lane.unit.id / run.name, dirs_exist_ok=True)

    def merge(self, lane: Lane, base: str) -> None:
        unit_id = lane.unit.id
        commits = self.git.run("rev-list", "--reverse", f"{base}..{lane.branch}", check=False).stdout.split()
        lane_store = UnitStore(lane.path / self.kernel.cr.units_dir.relative_to(self.kernel.root))
        if not commits:                        # not done: keep its state, and save its work for the next attempt
            lane_git = Git(lane.path)
            work = [p for p in lane_git.status() if not p.startswith(("log/", ".ralph/"))]
            lane_unit = lane_store.load().get(unit_id) if lane_store.exists() else None
            ref = vcs.save_rejected(lane_git, unit_id, lane_unit.attempts if lane_unit else 0, work) if work else ""
            self._adopt_state(lane_store, unit_id, f"A parallel attempt didn't finish; its work is saved under "
                                                   f"`{ref}`." if ref else "A parallel attempt didn't finish.")
            ui.warn(f"{unit_id}: the lane didn't finish it (see {lane.log}); it will run again.")
            return
        conflicts = self._conflicts(lane.branch)
        before = self.git.head()
        if conflicts:
            self._reject(lane, unit_id, f"its changes conflict with lanes merged before it ({', '.join(conflicts[:4])})")
            return
        result = self.git.run("cherry-pick", "--allow-empty", f"{base}..{lane.branch}", check=False)
        if result.returncode != 0:
            self.git.run("cherry-pick", "--abort", check=False)
            self._reject(lane, unit_id, "it didn't apply cleanly on top of the other lanes")
            return
        unit = self.kernel.store.load().get(unit_id)
        failed = self._gates_fail(unit) if unit and unit.status is Status.DONE else []
        if failed:
            self.git.run("reset", "--keep", before, check=False)
            self._reject(lane, unit_id, f"with the other lanes merged, gate {failed[0]} fails")
            return
        ui.ok(f"{unit_id} merged from its lane ({len(commits)} commit(s)).")

    def _conflicts(self, branch: str) -> list[str]:
        result = self.git.run("merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", branch, check=False)
        return [line for line in result.stdout.splitlines()[1:] if line.strip()] if result.returncode == 1 else []

    def _gates_fail(self, unit: Unit) -> list[str]:
        gates = gate_runner.select_gates(self.kernel.config.verify, unit)
        results = gate_runner.run_gates(gates, self.kernel.root, self.kernel.run_dir / "lanes" / f"{unit.id}-merged.log",
                                        self.kernel.config.verify.gate_timeout_minutes * 60)
        return [r.name for r in results if not r.passed]

    def _reject(self, lane: Lane, unit_id: str, why: str) -> None:
        ref = f"refs/ralph/rejected/{unit_id}/lane"
        self.git.run("update-ref", ref, lane.branch)
        store = self.kernel.store
        unit = store.load()[unit_id]
        unit.status, unit.wip_paths, unit.wip_overlap = Status.TODO, [], []
        store.save(unit)
        store.append_note(unit_id, "Parallel attempt set aside", f"It was done in its lane, but {why}. The lane's "
                          f"commits are saved under `{ref}`; the unit runs again on the current code.")
        ui.warn(f"{unit_id}: {why}; it will run again (lane work saved under {ref}).")

    def _adopt_state(self, lane_store: UnitStore, unit_id: str, note: str) -> None:
        """Copy an unfinished lane's unit state (attempts, notes, review gaps) to the main tree."""
        lane_unit = lane_store.load().get(unit_id) if lane_store.exists() else None
        if lane_unit is None:
            return
        lane_unit.wip_paths, lane_unit.wip_overlap = [], []      # that work isn't in your tree
        self.kernel.store.save(lane_unit)
        notes = read_text(lane_store.notes_path(unit_id))
        if notes:
            self.kernel.store.notes_path(unit_id).write_text(notes, encoding="utf-8")
        self.kernel.store.append_note(unit_id, "Parallel attempt", note)

    def remove(self, lane: Lane) -> None:
        self.git.run("worktree", "remove", "--force", str(lane.path), check=False)
        self.git.run("branch", "-D", lane.branch, check=False)
