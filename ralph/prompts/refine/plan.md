You are revising an implementation plan based on a critique report.

Rules:
- Apply only items in scope: {{SCOPE}}.
- Make STRUCTURAL edits: missing sections, missing rollback plans, broken dependencies, vague but fixable acceptance criteria, missing edge cases in test plans, missing units.
- Do NOT invent technical details that can't be derived from the plan and the critique. If an item needs information the plan doesn't contain, leave the plan unchanged for it and add it to `open_questions` instead.
- Keep unit IDs and unit names. Don't delete units, even if the critique suggests it: mark a unit deprecated in its entry instead and keep its body for the record.
- New units take the next free ID after {{LAST_ID}}. If you add any, also update the Implementation Order section (and Parallel Bundles, if the plan has one) through `sections`.
- Keep every unit small enough for one build session: about 8 key files at most. Split rather than grow a unit.

Return only what changes, in the structured result:
- `units`: for each unit you change, its complete new entry, starting with its unchanged `## Unit: Name (ID: Unnn)` header line.
- `sections`: for any other `## ` section you change, its exact current heading line and its complete new text.
- `new_units`: complete entries for the units you add, each starting with its `## Unit:` header.
- `open_questions`: critique items you can't resolve from the plan and the critique alone.
Whatever you leave out stays exactly as it is.

The units the critique cites are below in full; the rest of the plan is outlined. Open {{PLAN_PATH}} with Read if you need another part. Don't edit any file yourself.

---
CRITIQUE REPORT:
{{REPORT}}

---
UNITS THE CRITIQUE CITES:
{{CITED_UNITS}}

---
THE WHOLE PLAN (or its outline, when it's long):
{{PLAN}}
