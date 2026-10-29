"""Read-only Claude calls outside the build loop: drafting a spec or skills,
scoring a plan, running skill evals.

They use the same hermetic setup as loop runs (project MCP servers only,
project settings, no auto memory), never skip permissions, and get only the
tools they need (none, or read-only ones).
"""

from pathlib import Path

from ralph.prompts import schema as load_schema
from ralph.runner.base import AgentCall
from ralph.runner.settings import agent_env, project_mcp_config

READ_ONLY = ["Read", "Grep", "Glob"]


def analysis_call(root: Path, prompt: str, run_dir: Path, *, schema_name: str | None = None,
                  model: str = "sonnet", tools: list[str] | None = None, max_turns: int = 5,
                  max_budget_usd: float = 2.0, timeout_seconds: int = 600) -> AgentCall:
    run_dir.mkdir(parents=True, exist_ok=True)
    return AgentCall(
        prompt=prompt, cwd=root, model=model, log_path=run_dir / "stream.jsonl",
        max_turns=max_turns, max_budget_usd=max_budget_usd, timeout_seconds=timeout_seconds,
        json_schema=load_schema(schema_name) if schema_name else None,
        tools=[] if tools is None else tools,
        mcp_config=project_mcp_config(root, run_dir), env=agent_env(root, 200_000, hermetic=True),
    )
