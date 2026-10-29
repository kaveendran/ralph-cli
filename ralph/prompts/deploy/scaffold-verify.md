You are generating a POST-DEPLOY VERIFICATION checklist from an implementation plan and the spec's acceptance criteria.

{{RULES}}

Map each top-level acceptance criterion to one or more concrete observable checks. Each check is either:
  - a curl / API call (host as `<TODO: ...>`)
  - a UI flow (page URL `<TODO: ...>`, expected element or behaviour)
  - a metric check (metric name, expected range, dashboard URL `<TODO: ...>`)
  - a log assertion

The document:

# Verification: {{CR}}

## Acceptance Criteria Coverage
(numbered. For each AC: its text, the mapped check(s), the pass/fail criterion.)

## Smoke Tests (run first)
(5-8 minimal checks — basic auth, key endpoints, critical UI page)

## Deep Verification (run within 1 hour)
(broader functional checks against the spec)

## Monitoring Watch (run for 24 hours)
(metrics and alerts to watch, with expected baselines as `<TODO: ...>`)

---
SPEC ACCEPTANCE CRITERIA:
{{CRITERIA}}

{{TOOLS_NOTE}}

---
UNITS (from the harness, in build order):
{{UNITS}}

---
PLAN:
{{PLAN}}
