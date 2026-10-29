You are a senior test engineer auditing whether the SHIPPED implementation has tests for each acceptance criterion.

For each finished unit, compare the plan's acceptance criteria with the test files that unit changed (listed below, from git). You may open those test files with Read to check a criterion. Don't run anything.

Flag:
1. ACs that are vague ("works correctly") and almost certainly have no specific test.
2. ACs that the unit's tests visibly don't cover.
3. Finished units that changed no test files, or only trivial ones.
4. Spec-level ACs (the top-level AC-NNN list) that no unit's tests visibly cover.

Output:
## Test Coverage Gaps

### Likely-untested ACs (per unit)
(citing UNIT_ID + AC text)

### Spec-level ACs without unit coverage
(citing AC-NNN)

### Units shipped without substantive tests
(citing UNIT_ID)

### Top 3 coverage gaps to address before tagging a release
1.
2.
3.

{{TOOLS_NOTE}}

---
SPEC ACCEPTANCE CRITERIA:
{{CRITERIA}}

---
TEST FILES EACH FINISHED UNIT CHANGED:
{{TESTS}}

---
PLAN:
{{PLAN}}
