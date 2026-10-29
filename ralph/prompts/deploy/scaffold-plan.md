You are generating a DEPLOYMENT PLAN scaffold from an implementation plan.

{{RULES}}

The document has these top-level sections, in this order:

# Deployment Plan: {{CR}}

## Overview
(2-4 bullets summarizing the deploy)

## Environment
(table of env-specific TODOs the human MUST fill — cluster, region, namespace, secrets path, dashboards, flag service, on-call. Every value is `<TODO: ...>`.)

## Pre-deploy Gates
(numbered — checks that MUST be green before pressing go. Structural items from the plan; env-specific ones as `<TODO: ...>`.)

## Per-unit Deploy Steps
(one subsection per unit in build order: ### UNIT_ID — Name, then Type:, Risk:, Action:, Verification:, Notes:. Take risk and rollback hints from the unit's plan entry.)

## Rollout Strategy
(canary / blue-green / rolling — traffic % and dwell time as `<TODO: ...>`)

## Observability
(metrics, logs and alerts that MUST exist before deploy; env-specific paths as `<TODO: ...>`)

## Compliance & Audit
(change ticket, approvers, audit trail destination — env-specific as `<TODO: ...>`)

## Open Questions
(unanswered items from the plan's risks or assumptions)

{{TOOLS_NOTE}}

---
UNITS (from the harness, in build order):
{{UNITS}}

---
PLAN:
{{PLAN}}
