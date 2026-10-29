You are a senior engineering lead writing the retrospective for {{CR}}.

Produce ONE report combining the three analyses below. It MUST have exactly these top-level sections, in this order, with these exact headings (tooling splits the report at them):

# {{CR}} Retrospective

## Summary
(3–5 sentences: what shipped, headline risks, headline wins.)

## Plan vs Reality
(analysis 1, de-duplicated)

## Blocker Patterns
(analysis 2)

## Test Coverage Gaps
(analysis 3)

## Action Items Before Release
(numbered, each tagged MUST-FIX / SHOULD-FIX / NICE-TO-HAVE)

## RELEASE_NOTES draft
(markdown ready to paste into RELEASE_NOTES.md, with ### Features, ### Changes, ### Migrations, ### Known Issues. Pull from the finished units.)

## PR Description draft
(markdown ready to paste into a PR body, with ## Summary, ## Changes, ## Testing, ## Risk & Rollback. Reference unit IDs. This section is last.)

If an analysis is marked missing, say so in the Summary.

---
ANALYSIS 1 — PLAN VS REALITY:
{{PLAN_VS_REALITY}}

---
ANALYSIS 2 — BLOCKER PATTERNS:
{{BLOCKERS}}

---
ANALYSIS 3 — TEST COVERAGE:
{{COVERAGE}}
