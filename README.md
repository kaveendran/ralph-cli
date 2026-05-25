# ralph-cli

Bash CLI for bootstrapping and running the **Ralph Loop** — a headless Claude Code iteration system for autonomously implementing software change requests from written specs.

## Install

```bash
cd ralph-cli
./install.sh
```

Then reload your shell (`source ~/.bashrc`) and confirm:

```bash
ralph help
```

## Commands

### `ralph init`

Bootstrap the Ralph pattern into any project directory.

```bash
cd my-project
ralph init
```

**Flags (all optional):**

| Flag | Description |
|------|-------------|
| `--auto` | Skip mode prompt — Claude generates the initial spec from your brainstorm |
| `--manual` | Skip mode prompt — creates scaffold only; you write the spec yourself |
| `--project-dir PATH` | Target directory (defaults to `.`) |

**Interactive prompts:**
1. Project name and one-line description
2. Mode: **Auto** (Claude generates spec) or **Manual** (stub created)
3. Implementation type: `backend+frontend` · `backend` · `frontend` · `feature`
4. *(Auto mode only)* Tech stack and free-form brainstorm/goals/constraints
5. *(Backend types only)* Whether to generate Docker test infrastructure

**Auto mode** collects your tech stack and brainstorm, then calls Claude Sonnet (~30 seconds) to produce a fully structured 11-section specification. After scaffolding, it offers to immediately run the planning loop.

**Docker test infrastructure** (backend/backend+frontend only) generates:
- `docker-compose.test.yml` — isolated test environment
- `scripts/test-unit.sh`, `scripts/test-api.sh` — layered test runners
- `scripts/test-e2e.sh` + `e2e/playwright.config.ts` — E2E runner (backend+frontend)
- `.claude/settings.json` — Claude settings with Playwright MCP integration

**Creates if missing:**

```
AGENTS.md
CLAUDE.md
loop_plan.sh / loop_cr.sh
development/
├── templates/implementation-planning.md
├── shared/
└── initial/
    ├── spec/spec.md
    ├── plan/
    ├── progress/implementation-progress.md
    └── prompts/build.md, spec-planning.md
```

---

### `ralph add-cr`

Scaffold a new change request. Runs interactively if no flags are given.

```bash
ralph add-cr --number 5 --title "WDL Activity Extensions" --type backend
```

**Flags (all optional — prompts for missing ones):**

| Flag | Description |
|------|-------------|
| `--number N` | CR number (e.g. `22`) |
| `--title "..."` | Short title |
| `--type TYPE` | `backend` · `frontend` · `backend+frontend` · `feature` · `api` · `database` · `infrastructure` · `refactor` |
| `--prior-cr N` | Prior CR number — adds a baseline reference link in the build prompt |
| `--spec-file PATH` | Copy an existing file as the spec (instead of a blank stub) |
| `--notes "..."` | Special notes embedded in the build prompt |

**Creates under `development/crN/`:**
- `spec/spec.md` — spec stub (or copy of `--spec-file`)
- `prompts/build.md` — pre-filled build prompt with tech constraints and test commands
- `prompts/spec-planning.md` — pre-filled planning prompt
- `progress/implementation-progress.md` — empty progress tracker
- `plan/crN-implementation-plan.md` — plan file frontmatter

Test commands and tech constraints in the build prompt are auto-populated based on `--type` and whether `docker-compose.test.yml` exists in the project root.

After scaffolding, offers to update `loop_plan.sh` to point to the new CR's prompts.

---

### `ralph start`

Run the Ralph Loop.

```bash
# Generate implementation plan for CR5
ralph start --mode plan --cr 5

# Start implementation loop for CR5
ralph start --mode build --cr 5

# Cap at 10 iterations, use Opus
ralph start --mode build --cr 5 --max 10 --model opus

# Use an explicit prompt file
ralph start --mode build --prompt development/cr5/prompts/build.md
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `build` | `plan` or `build` |
| `--cr N` | — | CR number (auto-resolves prompt path) |
| `--prompt PATH` | — | Explicit prompt file (overrides `--cr`) |
| `--max N` | `0` (unlimited) | Stop after N iterations |
| `--model` | `opus` (plan) / `sonnet` (build) | Claude model |

If neither `--cr` nor `--prompt` is given, `ralph start` reads the prompt path from `loop_plan.sh` in the project root, or falls back to `development/initial/prompts/`.

**Plan mode** — reads `spec-planning.md` and orchestrates 20 subagents:
- 15 extraction agents split the spec into sections and extract structured requirements in parallel
- 5 synthesis agents produce: gap analysis, unit decomposition (P0–P3 priority + size + dependency graph), test strategy, risk analysis, and traceability matrix
- Writes the finished plan to `development/crN/plan/crN-implementation-plan.md`
- Model: Opus

**Build mode** — reads `build.md` and runs a continuous implementation loop:
- Each iteration prepends a compact **loop state header** (progress %, in-progress units, next unit, blocked units, last commit) generated fresh from the progress file
- Claude picks the highest-priority unblocked unit, implements it fully, runs the test suite, commits on green, and updates the progress file
- **Auto-stops** when every unit in the progress file is marked `[x]` complete
- Git-pushes after every iteration (creates remote tracking branch if needed)
- Logs each iteration to `log/<timestamp>-<prompt>-iteration-N.log`
- Model: Sonnet (up to 200 parallel subagents per iteration)

---

### `ralph status`

Show workflow stage and next steps for a CR.

```bash
ralph status --cr 5
ralph status          # auto-detects the highest-numbered CR with a progress file
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--cr N` | CR number (optional — auto-detects if omitted) |

Detects the current stage and prints context-aware guidance:

| Stage | Condition | What it shows |
|-------|-----------|---------------|
| `1-write-spec` | Spec missing or stub (<150 words) | Which sections to fill in |
| `2-plan` | Spec ready, no plan with units | Plan generation command + expected duration |
| `3-ready-to-build` | Plan generated, no build started | Plan review checklist before starting |
| `3-build` | Build in progress | Unit counts (complete / in-progress / not-started / blocked), progress % |
| `4-blocked` | Has `[!]` blocked units | Common causes + resolution steps |
| `5-done` | All units `[x]` | Final checklist (tests, git tag, AGENTS.md, PR) |

Also prints file status (line counts), the next command to run, and recent git commits.

---

### `ralph critique`

Council-of-experts plan review before starting the build loop.

```bash
ralph critique --cr 5
ralph critique        # auto-detects the latest CR with a plan
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--cr N` | CR number (optional — auto-detects if omitted) |

Spawns **5 parallel Sonnet subagents**, each reading the full plan from a distinct expert perspective:

| Expert | Focus |
|--------|-------|
| **Architect** | Unit size and decomposition, dependency graph correctness, scope clarity per unit, missing units, interface contracts |
| **Test Engineer** | Coverage gaps, untestable acceptance criteria, missing edge cases, test dependency issues, integration gaps |
| **Risk Analyst** | High-risk units, underspecified requirements, external dependency blockers, irreversible changes, integration risk |
| **Domain Expert** | Tech-stack-specific checks (API design, async/concurrency, data model for backend; component decomposition, state management, TypeScript types for frontend) |
| **Infrastructure Reviewer** | Docker healthcheck correctness, dependency chain (`condition: service_healthy`), hardcoded secrets, teardown `-v` hygiene, port/network conflicts with dev |

After all 5 complete, a **Synthesizer** deduplicates and prioritizes their findings into:

```
### CRITICAL — Fix before starting build
### IMPORTANT — Fix before starting build
### SUGGESTED — Consider before starting
### Plan health
```

---

## Typical workflow for a new CR

```bash
# 1. Scaffold the CR
ralph add-cr --number 6 --title "My Feature" --type backend+frontend

# 2. Write your spec
vi development/cr6/spec/spec.md

# 3. Check what's missing
ralph status --cr 6

# 4. Generate the implementation plan
ralph start --mode plan --cr 6

# 5. Review the plan
cat development/cr6/plan/cr6-implementation-plan.md

# 6. (Optional) Run the expert critique before building
ralph critique --cr 6

# 7. Start the implementation loop
ralph start --mode build --cr 6

# 8. Monitor progress
ralph status --cr 6
```

---

## The Ralph Loop document hierarchy

```
development/
├── templates/
│   └── implementation-planning.md   ← canonical planning template
├── shared/                          ← architecture docs, shared assets
├── initial/                         ← first-run CR (created by ralph init)
│   ├── spec/spec.md
│   ├── plan/full-implementation-plan.md
│   ├── progress/implementation-progress.md
│   └── prompts/build.md, spec-planning.md
└── crN/                             ← each change request (created by ralph add-cr)
    ├── spec/spec.md                  ← single source of truth
    ├── plan/crN-implementation-plan.md
    ├── progress/implementation-progress.md
    └── prompts/
        ├── build.md                  ← fed to `ralph start --mode build`
        └── spec-planning.md          ← fed to `ralph start --mode plan`
```

Two repo-root files all CRs maintain:
- **`AGENTS.md`** — purely operational: how to run, build, test
- **`CLAUDE.md`** — project standards (UI conventions, tech stack, etc.)

Iteration logs are written to `log/` in the project root:
```
log/<timestamp>-<prompt-name>-iteration-N.log
```

---

## Build loop rules enforced by default

These are injected into every `build.md` prompt:

1. After completing each unit, re-read the spec and plan before picking the next one.
2. Do not mark a unit `[x]` until every test in its Test Plan section passes unmodified.
3. If stuck on a single blocker for more than 3 tool-call rounds, mark the unit `[!] Blocked` with a clear reason and move on.
4. Never skip, xfail, or comment out failing tests — fix the root cause or mark the unit blocked.
5. Schema changes must be strictly additive unless the spec explicitly requires otherwise.

---

## Requirements

- Bash 4+ (Git Bash on Windows, or WSL)
- `claude` CLI in PATH (`npm install -g @anthropic-ai/claude-code`)
- `git` in PATH
- `awk`, `sed`, `date` (standard Unix tools available in Git Bash)
