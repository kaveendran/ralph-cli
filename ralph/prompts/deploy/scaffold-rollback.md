You are generating a ROLLBACK PROCEDURES document from an implementation plan.

{{RULES}}

Each unit's Rollback Plan section is given below verbatim. Reproduce it under that unit's heading. For a unit without one, write `**Rollback for UNIT_ID:** <TODO: missing in plan — author must add>`.

The document:

# Rollback Procedures: {{CR}}

## Decision Tree
(when to roll back vs roll forward — 4-6 bullets)

## Point of No Return
(the moments in the deploy AFTER which rollback becomes destructive or loses data. Derive them from the migration and api units.)

## Per-unit Rollback Procedures
(one subsection per unit, IN REVERSE deploy order — last deployed, first rolled back. Heading: ### UNIT_ID — Name. Body: the unit's Rollback Plan, plus a `**Verify rollback:**` line with `<TODO: smoke command>`.)

## Full-stack Rollback
(the catastrophic case — order of operations to revert everything. Env-specific commands as `<TODO: ...>`.)

---
ROLLBACK PLAN SECTIONS (verbatim, per unit):
{{ROLLBACKS}}

{{TOOLS_NOTE}}

---
UNITS (from the harness, in build order):
{{UNITS}}

---
PLAN:
{{PLAN}}
