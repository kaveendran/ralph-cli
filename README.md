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
./install.sh          # a symlink in ~/.local/bin or /usr/local/bin (or: pipx install .)
ralph doctor          # checks Claude Code, git and the project config
```

## Start a project

```bash
cd my-project && git init
ralph init                               # .ralph/config.toml, CLAUDE.md, development/initial/
#   (--test-infra also writes quiet test scripts and an isolated docker-compose.test.yml)
#   …write development/initial/spec/spec.md (number its sections: §1, §1.1, …)
#   …check the gates (your test commands) in .ralph/config.toml
git switch -c ralph/initial              # the loop won't commit to main
ralph start --mode plan --cr initial     # Claude writes the plan; the harness compiles it into units
ralph critique                           # (optional) 8 experts review the plan; refine-plan applies fixes
ralph graph                              # review the units and their order
ralph start --cr initial                 # the build loop
ralph watch                              # (another terminal) follow it live
```

`ralph init --brief "what you want to build" --stack "Python/FastAPI"` asks Claude to draft the spec for you to review. Later changes go through `ralph add-cr --number 2 --title "…"` (`--prior-cr 1` links the CR it builds on). `ralph status` tells you the next step at any point.

For a project that used the previous loop, run `ralph migrate --cr N --dry-run` to see what would change, then `ralph migrate --cr N` on a branch.

## What one iteration does

| Phase | What happens |
|---|---|
| Select | The next ready unit: one in progress first, then the plan's build order. A blocked unit only holds back the units that depend on it. |
| Impact check | Teammates' changes (as of your last `ralph sync`) that affect the unit. Local only; see [Working with a team](#working-with-a-team). |
| Prepare | A **context pack** with only the unit's own plan entry, the spec sections it cites, its dependencies' handoff notes, its last failure, your `ralph steer` notes, the checks it must pass, up to 5 unreviewed lessons that match its files, and 1–3 project skills that fit it. A few thousand tokens, however big the project gets. |
| Run | One `claude -p` session, **hermetic** (only the project's MCP servers and settings, no auto memory) and **bounded** (turns, budget, time, compaction at 200k tokens). Guardrail hooks stop the agent from committing, pushing or editing harness state. An `explorer` subagent handles codebase searches. |
| Verify | The harness runs the gates itself. An integrity check fails deleted tests, skip markers and frozen paths. For P0 and Large units, a separate read-only reviewer checks the tests against the acceptance criteria. |
| Commit | Only the files the unit changed, across all its attempts, plus its ledger entry. Your own uncommitted work is never swept in. After 3 failed attempts the unit is blocked, and its work is saved under `refs/ralph/rejected/`. |
| Record | One line per iteration in `log/iterations.jsonl`: turns, minutes, cost, peak context, compactions, gates, review. |

When every unit is done, a convergence check compares the implementation with the spec's acceptance criteria. Any gaps become new units; otherwise the CR is tagged `<cr>-done` locally. **The loop never pushes, fetches or rebases.** Publishing is up to you.

## Working with a team

Sync is manual: nothing reaches or leaves the remote unless you run a command.

```bash
ralph sync              # fetch; see what teammates landed and what it means for your units; rebase if you agree
ralph start --cr 2      # the loop works locally, with what your last sync brought in
ralph sync push         # review what would be published, then push
```

- **The ledger.** Each finished unit commits a small entry in `.ralph/ledger/`: the files it changed, the interfaces it changed (the agent's report, cross-checked by reading the code before and after), migrations, and a summary.
- **The impact check.** Before each unit, the loop compares teammates' entries with the unit's planned and changed files and the functions it uses, all locally:
  - **BREAKING** (an interface it uses changed) and **OVERLAP** (a file it plans to change changed) go into the unit's context. The agent acknowledges what it handled.
  - **DUPLICATE** (a teammate already finished this unit) and **CONFLICT** (its files won't merge with the shared branch) stop the loop until you `ralph sync`.
- **The shared branch** is your branch's upstream, or `[team] branch`. The loop reminds you when your last fetch is older than 12 hours; it never fetches itself.

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
| `ralph graph compile` | Plan → units, after you edit the plan. Rejects units with more than 8 key files unless `--allow-large`. `--analyze` also has Claude score each open unit's complexity (1–5) and propose splits for the hard ones (one read-only call, a few cents) |
| `ralph unblock ID` / `ralph set ID …` | Change a unit by hand; `set ID --status todo` accepts a triage unit |
| `ralph steer "…"` | A note for the next unit pack, without stopping the loop. `--hard "<cmd>"` adds a check every unit must pass |
| `ralph pause` / `ralph resume` | Let the current iteration finish, then wait |
| `ralph watch` | Follow the running loop: tool calls, context, cost. `--serve [--port 8765]` shows the same in a browser, on 127.0.0.1 only |
| `ralph sync` | Fetch the shared branch (the only network call), show what teammates landed and how it affects your open units, then rebase only if you say yes. `--no-rebase` just fetches and reports |
| `ralph sync push` | Show the commits, ledger entries and tags that would be published, then push after you confirm. Never forces |
| `ralph changes` / `ralph team` | What changed recently and who did it / per developer, plus the files more than one of you changed. As of your last sync |
| `ralph impact ID` | For an open unit: teammates' changes that affect it. For a finished one: the open units it affects |
| `ralph memory lint` | Size budgets for `CLAUDE.md`, `AGENTS.md`, rules, skills and unit notes (`--strict` for CI). Also checked at start |
| `ralph memory review` | Walk the lessons the agent reported, most-recurring first, and promote each one you approve: to a path-scoped rule, `CLAUDE.md`, or a skill. Leaves the edits uncommitted so you can reword them |
| `ralph memory decay` | Summarise the notes of units finished long ago (the full text moves to `archive/`) and move reviewed lessons out of the inbox |
| `ralph skills list` / `lint` | The project's skills and how often the loop used them / a check against Anthropic's skill authoring guidance |
| `ralph skills init --stack "…"` | Draft starter skills for your stack from your dependency versions (one call with no tools). You review them |
| `ralph skills refresh` | Flag skills and `docs/vendor/<lib>@<version>/` folders written for versions you no longer use |
| `ralph skills eval [name]` | Run each skill's scenarios (`.claude/skills/<name>/evals/*.json`) through Claude: did it use the skill, and does the answer contain what it should? |
| `ralph migrate --cr N` | Move a CR from the previous loop (keeps its statuses and notes) |
| `ralph doctor [--live]` | Check the setup; `--live` makes one tiny real call (about a cent) |
| `ralph critique` | 8 experts review the plan (architect, tester, risk, your stack, test infrastructure, security, performance, docs); a lead merges them into one CRITICAL / IMPORTANT / SUGGESTED report in `development/<cr>/critique/` |
| `ralph refine-plan` | Critique → proposed plan edits → you approve, skip or stop → critique again, at most `--max 3` rounds. Edits come back per unit, so units it doesn't touch stay exactly as they were |
| `ralph retro` | After the build: plan vs reality, blocker patterns across CRs, test-coverage gaps, plus draft `RELEASE_NOTES.md` and `PR_DESCRIPTION.md` |
| `ralph deploy scaffold` / `critique` / `refine` | Draft a deploy plan, runbook, rollback and verification checklist from the plan (environment details stay `<TODO: …>`); 6 deploy experts and a peer review give a GO / NO-GO; refine applies fixes you approve |

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

[memory]                   # size budgets, in lines (the defaults)
claude_md_lines = 200
rule_lines = 50
skill_lines = 500
learnings_in_pack = 5

[team]
# branch = "main"          # the branch you share (default: your branch's upstream)
impact_days = 7            # teammates' changes this recent are checked against each unit

[notify]                   # best-effort; a failure never changes what the loop does
# webhook = "https://hooks.slack.com/services/…"   # gets a JSON POST with a `text` field
# command = "terminal-notifier -message 'ralph needs you'"   # gets the event as JSON on stdin
events = ["blocked", "stopped", "complete", "usage_limit"]
```

## Project layout

```
.ralph/config.toml                     committed: harness settings
.ralph/learnings/inbox.jsonl           committed: lessons the agent reported (reviewed ones move to archive.jsonl)
.ralph/ledger/<time>-<cr>-<unit>.json  committed with each unit: what it changed, for teammates
.claude/skills/<name>/SKILL.md         committed: project skills (`ralph-versions:` records what they were written for)
.claude/rules/<area>.md                committed: path-scoped rules, e.g. promoted lessons
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

## Review workflows

`critique`, `refine-plan`, `retro` and `deploy` are a few read-only Claude calls each, at most four at a time, with `--model` to choose the model (default `runner.model_build`). The experts can open files with Read, Grep and Glob, but can't edit or run anything. What they're given stays bounded: a long plan becomes an outline with pointers into the file, and the retro reads unit state, metrics, the start of each unit's notes and the git log, not the full history. Ctrl-C stops every call. Logs are in `log/<workflow>-<time>/`.

## Team telemetry (optional)

Claude Code can export its own OpenTelemetry metrics and events (tokens, cost, tool results, compactions). Turn it on for every run ralph starts:

```toml
[telemetry]
otlp_endpoint = "http://localhost:4317"
protocol = "grpc"          # or http/json, http/protobuf
team = "payments"          # becomes the team.id attribute
```

ralph adds `developer` and `ralph.cr` attributes and shorter export intervals. Iterations join on `session.id`, which `log/iterations.jsonl` records too. Put credentials in `OTEL_EXPORTER_OTLP_HEADERS` in your environment, not in the config file. Prompt and tool content stays redacted unless you set Claude Code's `OTEL_LOG_*` variables yourself ([monitoring](https://code.claude.com/docs/en/monitoring-usage)).

## Development

```bash
PYTHONPATH=. python3 -P -m unittest discover -s tests -t .   # stdlib only, no Claude calls
```

## Projects from the previous loop

The previous bash loop is on ralph-cli's `main` branch. To move a CR over, run `ralph migrate --cr N --dry-run` to see what would change, then `ralph migrate --cr N` on a branch: it keeps each unit's status and notes, and leaves the old files in place.
