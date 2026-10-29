"""Turn on Claude Code's native OpenTelemetry export for the runs ralph starts.

Claude Code exports metrics (tokens, cost, lines of code, commits) and events
(API requests, tool results, compactions) itself; ralph only sets the
variables (code.claude.com/docs/en/monitoring-usage). Iterations join on the
`session.id` attribute, which ralph also records in log/iterations.jsonl.

Resource attributes stay low-cardinality (team, developer, CR, which loop),
and their values can't contain spaces, so they're percent-encoded. Export
intervals are shorter than the defaults because a run that gets stopped
loses what it hadn't exported yet.
"""

from urllib.parse import quote

from ralph.config import TelemetryConfig

METRIC_INTERVAL_MS = 10_000      # the default is 60 s
LOGS_INTERVAL_MS = 5_000


def otel_env(config: TelemetryConfig, **attributes: str) -> dict[str, str]:
    """The variables for one run, or {} when telemetry is off."""
    if not config.otlp_endpoint:
        return {}
    values = {"team.id": config.team, **{f"ralph.{k}" if k != "developer" else k: v for k, v in attributes.items()}}
    resource = ",".join(f"{key}={quote(value, safe='')}" for key, value in values.items() if value)
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": config.protocol,
        "OTEL_EXPORTER_OTLP_ENDPOINT": config.otlp_endpoint,
        "OTEL_METRIC_EXPORT_INTERVAL": str(METRIC_INTERVAL_MS),
        "OTEL_LOGS_EXPORT_INTERVAL": str(LOGS_INTERVAL_MS),
    }
    if resource:
        env["OTEL_RESOURCE_ATTRIBUTES"] = resource
    return env
