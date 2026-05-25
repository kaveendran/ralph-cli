# {{CR_NAME}} Build Prompt — {{CR_TITLE}}

> **Loop state:** The `## Loop State` block prepended above this prompt is auto-injected fresh each iteration by the loop runner. Read it before anything else — it tells you progress %, which units are in-progress (resume those first), the next unit to start, and any blocked units that need re-evaluation.

## 0. Sources of truth

Read before each unit; spec wins on behavior conflicts:

- **Spec:** [{{SPEC_PATH}}]({{SPEC_PATH}})
- **Plan:** [{{PLAN_PATH}}]({{PLAN_PATH}})
- **Project standards:** [CLAUDE.md](CLAUDE.md) — UI/frontend work binds to these standards.
- **Operational:** [AGENTS.md](AGENTS.md) — run/build/test commands.
{{PRIOR_CR_LINE}}

## 1. Task

Implement {{CR_NAME}} unit-by-unit per plan §"Implementation Order". Each unit's Goal / Requirements / Acceptance Criteria / Test Plan / DoD are binding.

- Pick the highest-priority unit whose deps are met and is not yet complete.
- Before writing any file: (1) read AGENTS.md to confirm run/build/test commands, (2) search for at least 2 existing files that use the pattern you are about to write, (3) match their structure exactly — do not invent new patterns.
- Implement fully — no placeholders, no TODOs, no stubs.
- Write the unit's tests in the same change. Tests are part of the deliverable.
- Run the relevant suite (§3); fix every failure, including unrelated pre-existing ones you uncover.

You may use up to 200 parallel Sonnet subagents for searches and reads, and up to 1 subagent for build/test execution.

## 2. Critical rules — read before every unit

{{CRITICAL_RULES}}

## 3. Progress tracking

Maintain [{{PROGRESS_PATH}}]({{PROGRESS_PATH}}). For each unit:

- **Unit ID** + one-line description (reference plan; do not duplicate).
- **Status:** `[ ] Not Started` / `[~] In Progress` / `[x] Complete` / `[!] Blocked`.
- **Date completed.**
- **Notes:** issues, deviations, follow-ups.

Update on start, completion, or block. Keep a PR-rollup summary at the top.

## 4. Testing — required after every unit

{{TEST_COMMANDS}}

**Never** skip, xfail, or disable a test to make it pass. If you uncover an unrelated regression, fix the root cause in the same increment.

## 5. Commit cadence

When a unit's tests pass and acceptance criteria are met:

1. Update the progress file (mark unit complete, date).
2. `git add -A && git commit -m "<message referencing unit IDs>"`
3. Git push is handled automatically by the loop runner — do not push manually.

**Completion gate** — when all units are `[x]` complete, before creating the git tag:
1. Spawn a subagent to verify each top-level acceptance criterion from the spec against the current implementation and produce a sign-off checklist (AC-001 ✓/✗ with evidence).
2. Fix any failing ACs before tagging.
3. Only then: `git tag {{GIT_TAG}}`.

## 6. Technology constraints — do not deviate

{{TECH_CONSTRAINTS}}

## 7. Scope guardrails

- Edits confined to files within the scope of {{CR_NAME}} (per plan §"In Scope").
- Do not add new dependencies without confirming with the user.
- Do not introduce feature flags — the PR sequencing IS the rollout.
- Schema changes must be strictly additive unless the spec explicitly requires otherwise.

## 8. When something is wrong or missing

- **New requirement or contradiction:** update the plan (via subagent — add a new unit or amend the affected unit's Requirements/ACs) and reflect the change in the progress file.
- **Spec contradicts observed behavior:** propose a spec update via a Sonnet subagent; do not proceed on the ambiguous unit until the spec is updated.
- **Blocked unit in the loop state header:** re-read the unit's Notes in the progress file. If the blocker is now resolved, change `[!]` back to `[ ]` and implement it. Only skip if the blocker is still live.
- **Operational knowledge** (new run/build/test commands, environment quirks): add to [AGENTS.md](AGENTS.md). Keep `AGENTS.md` purely operational.

## 9. Special notes

{{SPECIAL_NOTES}}
