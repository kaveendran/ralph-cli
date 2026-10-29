# ralph-cli

A meta-harness for Claude Code. It builds software from a written spec, **one bounded, verified unit of work per iteration**:

```
spec → plan → units → [ pick a unit → give Claude only that unit's context → run it, bounded
                        → the harness runs the tests → a read-only reviewer checks them → commit ] → repeat
```

The harness owns everything deterministic: which unit runs, what context Claude sees, the limits on each run, the test gates, the commits and the records. Claude owns the creative step: implementing the one unit in front of it. The design, the research behind it and the measurements that motivated it are in [docs/meta-harness-plan.md](docs/meta-harness-plan.md).

## Install

Needs Python 3.11+, git, and [Claude Code](https://code.claude.com) on your PATH. No other dependencies.

```bash
cd ralph-cli
./install.sh          # an alias, or a symlink in /usr/local/bin
ralph doctor          # checks Claude Code, git and the project config
```

## Start a project

```bash
cd my-project && git init
ralph init                               # .ralph/config.toml, CLAUDE.md, development/initial/
#   …write development/initial/spec/spec.md (number its sections: §1, §1.1, …)
#   …check the gates (your test commands) in .ralph/config.toml
git switch -c ralph/initial              # the loop won't commit to main
ralph start --mode plan --cr initial     # Claude writes the plan; the harness compiles it into units
ralph graph                              # review the units and their order
ralph start --cr initial                 # the build loop
ralph watch                              # (another terminal) follow it live
```

`ralph init --brief "what you want to build" --stack "Python/FastAPI"` asks Claude to draft the spec for you to review. Later changes go through `ralph add-cr --number 2 --title "…"`.

For a project that used the previous loop, run `ralph migrate --cr N --dry-run` to see what would change, then `ralph migrate --cr N` on a branch.

## What one iteration does

| Phase | What happens |
|---|---|
| Select | The next ready unit: one in progress first, then the plan's build order. A blocked unit only holds back the units that depend on it. |
| Prepare | A **context pack** with only the unit's own plan entry, the spec sections it cites, its dependencies' handoff notes, its last failure, your `ralph steer` notes and the checks it must pass. A few thousand tokens, however big the project gets. |
| Run | One `claude -p` session, **hermetic** (only the project's MCP servers and settings, no auto memory) and **bounded** (turns, budget, time, compaction at 200k tokens). Guardrail hooks stop the agent from committing, pushing or editing harness state. An `explorer` subagent handles codebase searches. |
| Verify | The harness runs the gates itself. An integrity check fails deleted tests, skip markers and frozen paths. For P0 and Large units, a separate read-only reviewer checks the tests against the acceptance criteria. |
| Commit | Only the files the unit changed, across all its attempts. Your own uncommitted work is never swept in. After 3 failed attempts the unit is blocked, and its work is saved under `refs/ralph/rejected/`. |
| Record | One line per iteration in `log/iterations.jsonl`: turns, minutes, cost, peak context, compactions, gates, review. |

When every unit is done, a convergence check compares the implementation with the spec's acceptance criteria. Any gaps become new units; otherwise the CR is tagged `<cr>-done` locally. **The loop never pushes, fetches or rebases.** Publishing is up to you.

## Commands

| Command | What it does |
|---|---|
| `ralph init` / `ralph add-cr` | Set up a project / a change request |
| `ralph start --cr N` | The build loop. `--max N`, `--unit ID`, `--model`, `--max-turns`, `--max-budget`, `--review off\|p0-and-large\|all` |
| `ralph start --mode plan --cr N` | Write the plan from the spec, then compile it into units |
| `ralph start --task "…"` | One bounded iteration on work outside the plan, e.g. a bug fix |
| `ralph start --dry-run` | The next unit's context pack and its size, and the exact command. Runs nothing |
| `ralph status` | Progress, what runs next, blocked and triage units, per-unit time, cost and context. `--write` generates `PROGRESS.md` |
| `ralph graph` / `ralph ready` | The units and their dependencies (`--mermaid`, `--json`) / what can start now |
| `ralph graph compile` | Plan → units, after you edit the plan. Rejects units with more than 8 key files unless `--allow-large` |
| `ralph unblock ID` / `ralph set ID …` | Change a unit by hand; `set ID --status todo` accepts a triage unit |
| `ralph steer "…"` | A note for the next unit pack, without stopping the loop. `--hard "<cmd>"` adds a check every unit must pass |
| `ralph pause` / `ralph resume` | Let the current iteration finish, then wait |
| `ralph watch` | Follow the running loop: tool calls, context, cost |
| `ralph migrate --cr N` | Move a CR from the previous loop (keeps its statuses and notes) |
| `ralph doctor [--live]` | Check the setup; `--live` makes one tiny real call (about a cent) |
| `ralph critique`, `refine-plan`, `retro`, `deploy` | Plan review and delivery workflows (still the original bash scripts) |
| `ralph legacy <command>` | Run any original bash command, e.g. the previous loop for a CR that isn't migrated |

## Configuration

`.ralph/config.toml` (written by `ralph init` and `ralph migrate`):

```toml
[runner]
model_build = "sonnet"
max_turns = 200            # per iteration
max_budget_usd = 30
compact_window = 200000
# claude_version = "2.1.311"   # pin it: the loop stops if Claude Code changes

[verify]
review = "p0-and-large"
frozen = []                # globs the agent must never change
paid_gates = []            # gates that only run for units with "Spend: paid"

[verify.gates]             # what decides "done"
unit = "bash scripts/test-unit.sh"
api = "bash scripts/test-api.sh"

[verify.by_type]
default = ["unit"]
api = ["unit", "api"]
```

## Project layout

```
.ralph/config.toml                     committed: harness settings
.ralph/learnings/inbox.jsonl           committed: lessons the agent reported
development/<cr>/spec/spec.md          you write it
development/<cr>/plan/…-plan.md        plan mode writes it; you review it
development/<cr>/units/U012.json       unit state, written only by the harness
development/<cr>/units/U012.notes.md   failures, review gaps and handoffs per unit
development/<cr>/prompts/instructions.md   CR rules included in every unit pack
log/                                   ignored: iterations.jsonl and per-iteration run folders
```

## Safety

- **Runs are hermetic and bounded**, and the harness commits only the unit's own files.
- **The guardrail hooks prevent accidents, not deliberate misuse.** The agent runs with permissions skipped so it can work unattended, so for overnight or unattended runs use a container or the OS sandbox.

## Development

```bash
PYTHONPATH=. python3 -P -m unittest discover -s tests -t .   # the harness (stdlib only)
bash test/e2e.sh                                              # the original bash commands
```

## The previous loop

The original bash commands are still here, under `commands/`, and run with `ralph legacy <command>` (or `RALPH_LEGACY=1 ralph <command>`). `ralph legacy start --cr N` is the previous loop, already improved with the same quick fixes:
- compaction at 200k
- hermetic runs
- turn and budget caps
- one unit per iteration
- the explorer subagent
- telemetry in `log/index.jsonl`
- no automatic pushes

Its extra flags:

| Flag | Default | Description |
|---|---|---|
| `--max-turns N` / `--max-budget USD` | `200` / `30` (plan: `300` / `60`) | Caps per iteration |
| `--compact-window N` | `200000` | Auto-compact window |
| `--setting-sources LIST` | `project,local` | Claude settings to load |
| `--no-hermetic` | — | Use your full personal Claude setup |
| `--push` | off | Push after each iteration |
| `--dry-run` | — | Show the next unit, prompt size and command |
