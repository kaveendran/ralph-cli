"""Project configuration from `.ralph/config.toml`, with defaults.

Every setting has a default, so a project without a config file still works.
Unknown keys are an error rather than being ignored, so a typo doesn't
silently fall back to a default.
"""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from ralph.errors import RalphError


@dataclass
class RunnerConfig:
    model_build: str = "sonnet"
    model_plan: str = "opus"
    max_turns: int = 200
    max_budget_usd: float = 30.0
    timeout_minutes: int = 90
    silence_minutes: int = 10           # no output for this long means the run is stuck
    compact_window: int = 200_000
    setting_sources: str = "project,local"
    hermetic: bool = True
    permissions: str = "bypass"         # bypass | strict
    claude_version: str = ""            # pin, e.g. "2.1.311"; empty means don't check


@dataclass
class VerifyConfig:
    gates: dict[str, str] = field(default_factory=dict)        # name → shell command
    by_type: dict[str, list[str]] = field(default_factory=dict)  # unit type → gate names
    paid_gates: list[str] = field(default_factory=list)        # only for units with "Spend: paid"
    health: str = ""                    # quick command before each iteration
    done: str = ""                      # must pass before a CR counts as complete
    frozen: list[str] = field(default_factory=list)            # globs the agent must never change
    review: str = "p0-and-large"        # off | p0-and-large | all
    max_attempts: int = 3
    gate_timeout_minutes: int = 30


@dataclass
class LoopConfig:
    max_stalls: int = 3                 # iterations in a row with no progress before stopping
    max_invalid_results: int = 3        # unusable agent results in a row before stopping
    max_agent_failures: int = 3         # failed claude runs in a row before stopping
    retry_delay_seconds: int = 60       # doubles after each failure
    protect_branches: list[str] = field(default_factory=list)  # empty: the remote's default branch
    tag_format: str = "{cr}-done"       # local tag when a CR converges; "" turns tagging off
    converge: bool = True               # check the finished CR against the spec's acceptance criteria


@dataclass
class MemoryConfig:
    """Size budgets for what Claude loads (docs/meta-harness-plan.md §8). Over
    budget is a warning at the start of each run, and an error with
    `ralph memory lint --strict`."""
    claude_md_lines: int = 200
    agents_md_lines: int = 200
    rule_lines: int = 50
    skill_lines: int = 500
    notes_lines: int = 200
    learnings_in_pack: int = 5


@dataclass
class NotifyConfig:
    webhook: str = ""                   # POST a JSON message here (a Slack incoming webhook works)
    command: str = ""                   # or run this with the event JSON on stdin
    events: list[str] = field(default_factory=lambda: ["blocked", "stopped", "complete", "usage_limit"])


@dataclass
class TeamConfig:
    """Manual team sync (docs/meta-harness-plan.md §11). Only `ralph sync` and
    `ralph sync push` touch the remote; the loop reads what's already local."""
    remote: str = "origin"
    branch: str = ""                    # the shared branch; default: your branch's upstream
    ledger: bool = True                 # write .ralph/ledger/ entries with each unit's commit
    impact_days: int = 7                # teammates' entries this recent count for the impact check
    stale_sync_hours: int = 12          # remind you to `ralph sync` when the last fetch is older


@dataclass
class TelemetryConfig:
    """Claude Code's own OpenTelemetry export for every run (docs/meta-harness-plan.md §10.4).
    Off until `otlp_endpoint` is set. Put credentials in OTEL_EXPORTER_OTLP_HEADERS in your
    environment, never in this file."""
    otlp_endpoint: str = ""             # e.g. "http://localhost:4317"
    protocol: str = "grpc"              # grpc | http/json | http/protobuf
    team: str = ""                      # sent as the team.id resource attribute


@dataclass
class Config:
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    team: TeamConfig = field(default_factory=TeamConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)


def _apply(target, values: dict, where: str) -> None:
    known = {f.name: f for f in fields(target)}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise RalphError(
            f"Unknown setting(s) in [{where}] of .ralph/config.toml: {', '.join(unknown)}",
            f"Known settings: {', '.join(sorted(known))}",
        )
    for name, value in values.items():
        setattr(target, name, value)


def load_config(config_path: Path) -> Config:
    """The project's config, or the defaults when the file doesn't exist."""
    config = Config()
    if not config_path.exists():
        return config
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as problem:
        raise RalphError(f"{config_path} isn't valid TOML: {problem}") from None

    sections = {"runner": config.runner, "verify": config.verify, "loop": config.loop,
                "memory": config.memory, "notify": config.notify, "team": config.team,
                "telemetry": config.telemetry}
    unknown = sorted(set(data) - set(sections))
    if unknown:
        raise RalphError(f"Unknown section(s) in {config_path}: {', '.join(unknown)}",
                         f"Known sections: {', '.join(sections)}")
    for name, target in sections.items():
        _apply(target, data.get(name, {}), name)
    return config
