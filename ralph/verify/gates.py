"""Run the project's test commands ("gates") after an agent run.

The harness runs them itself rather than taking the agent's word for it.
The full output goes to a log file; only the failing tail is kept for the
next attempt's context. A failing gate is retried once, to rule out a flaky
test. Infrastructure trouble (Docker not running, a port in use) is reported
as such: it stops the loop but doesn't count against the unit.
"""

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ralph.config import VerifyConfig
from ralph.fsutil import tail_lines
from ralph.model.unit import Unit

INFRA_PATTERNS = re.compile(
    r"Cannot connect to the Docker daemon|Is the docker daemon running|docker: command not found"
    r"|port is already allocated|address already in use|EADDRINUSE|bind: address already in use"
    r"|No space left on device",
    re.IGNORECASE,
)
INFRA_EXIT_CODES = {125, 126, 127}   # docker run failed, not executable, command not found
TAIL_LINES = 60


@dataclass
class GateResult:
    name: str
    command: str
    passed: bool
    exit_code: int
    seconds: float
    log_path: Path
    tail: str
    infra_error: bool = False
    passed_on_retry: bool = False


def select_gates(config: VerifyConfig, unit: Unit | None) -> list[tuple[str, str]]:
    """(name, command) pairs for a unit: its type's gates (or the defaults),
    plus the paid gates when the unit spends money."""
    names = list(config.by_type.get(unit.type, []) if unit else [])
    if not names:
        names = list(config.by_type.get("default", [])) or list(config.gates)
    if unit and unit.spend == "paid":
        names += [g for g in config.paid_gates if g not in names]
    elif config.paid_gates:
        names = [n for n in names if n not in config.paid_gates]
    return [(name, config.gates[name]) for name in names if name in config.gates]


def _run_once(name: str, command: str, cwd: Path, log_path: Path, timeout_s: int) -> GateResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        output, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired as expired:
        output = f"{expired.stdout or ''}{expired.stderr or ''}\n[ralph: gate timed out after {timeout_s}s]"
        code = 124
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"$ {command}\n{output}\n[exit {code}]\n\n")
    infra = code in INFRA_EXIT_CODES or bool(INFRA_PATTERNS.search(output))
    return GateResult(name, command, code == 0, code, time.monotonic() - started, log_path,
                      tail_lines(output, TAIL_LINES), infra_error=infra and code != 0)


def run_gate(name: str, command: str, cwd: Path, log_path: Path, timeout_s: int,
             retry: bool = True) -> GateResult:
    result = _run_once(name, command, cwd, log_path, timeout_s)
    if result.passed or result.infra_error or not retry:
        return result
    again = _run_once(name, command, cwd, log_path, timeout_s)
    again.passed_on_retry = again.passed
    return again


def run_gates(gates: list[tuple[str, str]], cwd: Path, log_path: Path, timeout_s: int) -> list[GateResult]:
    """Run gates in order, stopping at the first failure (fail fast)."""
    results = []
    for name, command in gates:
        result = run_gate(name, command, cwd, log_path, timeout_s)
        results.append(result)
        if not result.passed:
            break
    return results


def failure_summary(results: list[GateResult]) -> str:
    """The failing gate's command and output tail, for the next attempt's context."""
    failed = [r for r in results if not r.passed]
    if not failed:
        return ""
    first = failed[0]
    return f"Gate `{first.name}` failed (exit {first.exit_code}): `{first.command}`\n\n```\n{first.tail}\n```"
