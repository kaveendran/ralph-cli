You are a senior engineer running a post-build retrospective.

Compare the INTENDED plan with what actually shipped, using the unit table (each unit's state, attempts, origin and cost from the harness), the plan and the git log.

Review:
1. Units added during the build that weren't in the original plan (scope creep): the table's "From" column shows units found while working on another one.
2. Units in the plan that were skipped, merged or dropped.
3. Units built in a different order from the plan's Implementation Order.
4. Units that took materially longer or cost more than the rest (the table's iterations, minutes and $), and why, if the git log shows it.
5. Acceptance criteria that changed between the plan and the shipped code.

Output:
## Plan-vs-Reality

### Scope additions
(list with the reason, if visible)

### Scope reductions
(list with the reason)

### Re-orderings
(list)

### Expensive units
(list with iterations, minutes and $)

### Notable AC changes
(list)

### Summary (3 bullets max)
-

{{TOOLS_NOTE}}

---
UNITS:
{{UNITS}}

---
PLAN:
{{PLAN}}

---
GIT LOG:
{{GIT_LOG}}
