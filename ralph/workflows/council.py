"""Ask several experts in parallel, then have one lead merge what they said.

Each expert is a read-only Claude call (Read, Grep and Glob; no edits, no
shell), so it can open the parts of the plan or code its prompt only
points to. At most four run at once. Ctrl-C stops them all: each call runs
in its own process group, which the terminal's Ctrl-C doesn't reach, so the
council cancels them itself. An expert that returns nothing is reported
and left out, never passed on as an empty review.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ralph import ui
from ralph.errors import RalphError
from ralph.runner.analysis import READ_ONLY, analysis_call
from ralph.runner.base import Runner

MAX_PARALLEL = 4
TOOLS_NOTE = ("You have read-only tools (Read, Grep, Glob) to check the plan file or the code when something "
              "needs it. Don't edit anything, and keep your answer to the format above.")


@dataclass
class Expert:
    name: str
    prompt: str
    tools: list[str] = field(default_factory=lambda: list(READ_ONLY))
    schema: str = ""              # a structured result, e.g. "document.json"…
    result_key: str = ""          # …and which of its fields holds the text


@dataclass
class Opinion:
    name: str
    text: str
    cost_usd: float = 0.0
    seconds: float = 0.0
    problem: str = ""             # why there's no text


@dataclass
class Council:
    runner: Runner
    root: Path
    run_dir: Path
    model: str = "sonnet"
    env: dict[str, str] = field(default_factory=dict)      # e.g. telemetry variables
    max_turns: int = 10
    max_budget_usd: float = 2.0

    def _ask_one(self, expert: Expert, cancel: threading.Event, max_turns: int, budget: float) -> Opinion:
        started = time.monotonic()
        call = analysis_call(self.root, expert.prompt, self.run_dir / expert.name, model=self.model,
                             tools=expert.tools, max_turns=max_turns, max_budget_usd=budget, timeout_seconds=900,
                             schema_name=expert.schema or None)
        call.cancel = cancel
        call.env.update(self.env)
        result = self.runner.run(call)
        if expert.schema:
            text = str((result.structured or {}).get(expert.result_key) or "").strip()
        else:
            text = str(result.result.get("result") or result.stats.last_text or "").strip()
        cost = float(result.result.get("total_cost_usd") or 0)
        problem = "" if text else f"returned nothing ({result.subtype or result.stopped_by or f'exit {result.exit_code}'})"
        return Opinion(expert.name, text, cost, time.monotonic() - started, problem)

    def ask(self, experts: list[Expert]) -> dict[str, Opinion]:
        """Every expert's opinion, in the order given. Reports each one as it finishes."""
        cancel = threading.Event()
        opinions: dict[str, Opinion] = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            futures = {pool.submit(self._ask_one, e, cancel, self.max_turns, self.max_budget_usd): e for e in experts}
            try:
                for future in as_completed(futures):
                    expert = futures[future]
                    try:
                        opinion = future.result()
                    except RalphError as problem:
                        opinion = Opinion(expert.name, "", problem=problem.message)
                    opinions[expert.name] = opinion
                    if opinion.text:
                        ui.ok(f"{opinion.name} · {opinion.seconds:.0f} s · ${opinion.cost_usd:.2f}")
                    else:
                        ui.warn(f"{opinion.name} {opinion.problem}; left out (log: {self.run_dir / opinion.name})")
            except KeyboardInterrupt:
                cancel.set()                  # running calls stop within a second or so
                for future in futures:
                    future.cancel()           # queued ones never start
                raise
        if not any(o.text for o in opinions.values()):
            raise RalphError("None of the experts returned a review.", f"See the logs in {self.run_dir}.")
        return {e.name: opinions[e.name] for e in experts}

    def lead(self, name: str, prompt: str) -> Opinion:
        """The one call that merges the experts' reviews. It needs no tools."""
        opinion = self._ask_one(Expert(name, prompt, tools=[]), threading.Event(), max_turns=3, budget=3.0)
        if not opinion.text:
            raise RalphError(f"The {name} step {opinion.problem}.", f"See {self.run_dir / name / 'stream.jsonl'}.")
        return opinion

    def structured(self, name: str, prompt: str, schema_name: str, max_turns: int = 12) -> tuple[dict, float]:
        """One read-only call with a structured result, e.g. proposed plan edits."""
        call = analysis_call(self.root, prompt, self.run_dir / name, schema_name=schema_name, model=self.model,
                             tools=list(READ_ONLY), max_turns=max_turns, max_budget_usd=3.0, timeout_seconds=900)
        call.env.update(self.env)
        result = self.runner.run(call)
        if result.structured is None:
            raise RalphError(f"The {name} step returned no result ({result.subtype or f'exit {result.exit_code}'}).",
                             f"See {self.run_dir / name / 'stream.jsonl'}.")
        return result.structured, float(result.result.get("total_cost_usd") or 0)


def reviews(opinions: dict[str, Opinion], titles: dict[str, str]) -> dict[str, str]:
    """Template values: each expert's text, or a line saying why it's missing."""
    return {key: opinions[name].text if name in opinions and opinions[name].text
            else f"(The {name} review is missing: {opinions[name].problem if name in opinions else 'not run'}.)"
            for name, key in titles.items()}


def total_cost(*groups) -> float:
    return sum(o.cost_usd for group in groups for o in (group.values() if isinstance(group, dict) else [group]))
