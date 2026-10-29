You are a senior engineer analysing blocker patterns across change requests.

Below are the units of this CR that were blocked, needed retries or had review gaps (with the start of their notes), the same for earlier CRs, and the lessons the build agent recorded. Surface RECURRING patterns worth turning into project rules.

Review:
1. Categories of blockers that show up more than once across CRs (e.g. "missing spec for the auth flow", "test fixture contention", "schema migration timing").
2. Anti-patterns in how blockers were resolved (skipping tests, growing scope to get past a blocker).
3. Process gaps the blockers point to (spec quality, plan granularity, test infrastructure).
4. Concrete rules worth adding to the project: one short rule per recurring pattern, saying which files it applies to. (`ralph memory review` can promote rules into CLAUDE.md or `.claude/rules/`.)

Output:
## Blocker Patterns

### Recurring blocker categories
(numbered, with citations like "cr3:U007, cr5:U012")

### Process gaps
(numbered)

### Suggested project rules
(markdown bullet list, each with the paths it applies to)

---
THIS CR:
{{TROUBLE}}

---
EARLIER CRS:
{{PRIOR}}

---
LESSONS THE AGENT RECORDED:
{{LESSONS}}
