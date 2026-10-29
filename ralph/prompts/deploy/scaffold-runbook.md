You are generating a DEPLOYMENT RUNBOOK scaffold from an implementation plan.

{{RULES}}

A runbook is a numbered, ordered checklist a human follows during the deploy. The document:

# Runbook: {{CR}}

## 0. Pre-flight
(numbered steps — checks before touching anything. Env-specific commands as `<TODO: ...>`.)

## 1. Deploy Steps (in order)
(numbered. One step per unit OR per logical phase. Each step:
  - **Action:** what to run / click
  - **Expected:** observable success signal
  - **If wrong:** branch to the rollback.md section for it
  - **Owner:** `<TODO: role/person>`
Use the build order. Migration units come BEFORE the API/frontend units that depend on them.)

## 2. Smoke & Verify
(reference verify.md)

## 3. Post-deploy
(numbered — monitoring window, cleanup, announce)

{{TOOLS_NOTE}}

---
UNITS (from the harness, in build order):
{{UNITS}}

---
PLAN:
{{PLAN}}
