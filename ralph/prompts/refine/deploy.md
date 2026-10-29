You are revising a deploy plan based on a critique report.

Rules:
- Apply only items in scope: {{SCOPE}}.
- Make STRUCTURAL edits only. Allowed examples: adding a missing pre-deploy gate, adding a missing per-unit step, fixing migration ordering, adding a missing rollback reference, naming an absent approver role, adding a missing observability hook.
- DO NOT invent environment-specific content. If an item needs a value the deploy plan doesn't contain (cluster name, secret path, dashboard URL, on-call contact), leave a `<TODO: ...>` marker rather than guessing.
- Keep the unit IDs and the order of the sections.
- Return the COMPLETE revised deploy plan as `document`. No preamble, no diff.

---
CRITIQUE REPORT:
{{REPORT}}

---
CURRENT DEPLOY PLAN:
{{DOCUMENT}}
