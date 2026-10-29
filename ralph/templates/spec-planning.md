# PLAN-ONLY Implementation Planning Prompt
(Spec-Driven, Generic, Token-Efficient, Reusable)

You are operating in PLAN-ONLY mode.

Do NOT implement any code. This prompt generates implementation plans from specification documents.

---

## 📝 Input Parameters

### Required Parameters:
1. **SPEC_FILE**: `{{SPEC_PATH}}`

2. **IMPLEMENTATION_TYPE**: `{{IMPLEMENTATION_TYPE}}`

3. **OUTPUT_PATH**: Where to write the implementation plan
   - Format: `{{PLAN_PATH}}`

4. **SPECIAL NOTES**: {{SPECIAL_NOTES}}

### Optional Parameters:
- **EXISTING_CODE**: {{EXISTING_CODE}}


> **Harness note.** The ralph harness reads this plan: it picks units by the `## Implementation Order` table, gives each unit only its own entry plus the spec sections in `Spec refs`, and tracks status itself. Don't add per-unit status fields; keep every unit to one focused session.

---

## Core Objectives

- Produce a unit-based implementation plan from ANY specification or CR document.
- Units must be independently buildable, reviewable, and testable.
- Testing must be planned as first-class work.
- Use ONLY Sonnet subagents for all tasks (extraction + synthesis).
- Keep this prompt reusable across all project components.

---

## Token Efficiency Rules

- Sonnet subagents perform parallel extraction and distributed synthesis.
- All outputs must be structured lists only.
- Do not quote spec text — use section pointers like "Spec 4.2" or "CR Section 3.1.2".
- The main agent only merges summaries and writes the plan file.
- Each subagent focuses on a single concern.

---

## Phase 0: Input Discovery (Main Agent)

1. **Validate Input Parameters**
   - Confirm SPEC_FILE exists and is readable
   - Confirm IMPLEMENTATION_TYPE is valid
   - Confirm OUTPUT_PATH destination

2. **Read Context**
   - Read README.md (if it exists) to understand:
     - Project purpose
     - User personas
     - Domain vocabulary
     - High-level constraints
     - Technology stack
   - Read AGENTS.md (if it exists) to extract:
     - Test commands (unit, integration, E2E)
     - Build and run commands
     - Environment setup requirements
     - Pass extracted test commands to S3 (Test Strategy Definition)
   - Read SPEC_FILE to identify:
     - Section structure
     - Scope and requirements
     - Dependencies and constraints

3. **Check Existing Plans**
   - Check if OUTPUT_PATH already exists
   - If exists, determine if this is an update or new plan

4. **Determine Test Types**
   - Based on IMPLEMENTATION_TYPE, identify relevant test types:
     - **frontend**: Component, Integration, E2E, Visual Regression, A11y
     - **backend**: Unit, Integration, API Contract, Performance
     - **api**: Contract, Integration, Load, Security
     - **database**: Migration, Data Integrity, Performance
     - **infrastructure**: Integration, Smoke, Health Check
     - **feature**: All applicable types from above
     - **refactor**: Regression suite from existing tests

Do not summarize the spec here. Only identify structure.

---

## Phase 1: Parallel Requirement Extraction (Sonnet Subagents)

Spawn up to 15 Sonnet subagents to fully extract all implementable requirements from SPEC_FILE.

**Distribution Strategy:**
- Divide spec into logical sections (e.g., 5-10 sections per agent)
- Ensure complete coverage with no gaps
- Allow agents to overlap slightly to catch cross-cutting concerns

Each Sonnet subagent must:
- Declare its focus area in one short line
- Output structured lists only
- Use section pointers instead of quoting text
- Identify dependencies on other sections

### Sonnet Extraction Output Format (Strict)

```markdown
### Agent N: [Focus Area]

Focus:
- <one line describing section coverage>

Requirements Extracted:
- <structured bullet lists by category>
- Use format: [Category] Requirement description (Spec X.Y)

Dependencies Identified:
- Depends on <other spec sections or external systems>

Technical Constraints:
- <technology, performance, security constraints>

Spec References:
- Spec <section pointers>
```

---

## Phase 2: Distributed Synthesis (Sonnet Subagents)

Spawn exactly 5 Sonnet synthesis subagents (S1–S5). Each agent has a single, specific responsibility.

### Agent S1: Gap Analysis and Validation

**Responsibility:** Analyze all extraction outputs to find spec issues.

**Tasks:**
- Identify missing requirements (referenced but not defined)
- Identify ambiguous requirements (multiple interpretations)
- Identify contradictory requirements (conflicts between sections)
- Identify assumptions that must be made
- Propose minimal spec additions only for true blockers

**Output Format:**
```markdown
### Synthesis Agent 1: Gap Analysis

Spec Gaps (Blockers Only):
- Gap: <description>
  Impact: <what cannot be implemented>
  Location: <where in spec to add>
  Proposed Addition: <minimal text>

Ambiguities (Require Clarification):
- Item: <description>
  Interpretations: <option A, option B>
  Recommendation: <preferred interpretation>
  Rationale: <why>

Assumptions (Unavoidable):
- Assumption: <what we assume>
  Reason: <why needed>
  Validation: <how to verify>
  Risk: <if wrong>
```

### Agent S2: Unit Decomposition

**Responsibility:** Break requirements into atomic implementation units.

**Tasks:**
- Group related requirements into logical units
- Ensure each unit fits one focused agent session: at most 8 key files and one commit. Split anything bigger
- Define clear unit boundaries
- Identify unit dependencies
- Assign priorities based on dependencies and risk
- Target 8–20 units total; if count exceeds 20, merge units smaller than 0.5 days each

**Output Format:**
```markdown
### Synthesis Agent 2: Unit Decomposition

Implementation Units:

Unit ID: <U001, U002, etc.>
Name: <short descriptive name>
Type: <component|service|api|migration|config|test>
Priority: <P0-Critical|P1-High|P2-Medium|P3-Low>
Size Estimate: <Small|Medium|Large>
Depends On: [<unit IDs>]
Enables: [<unit IDs that depend on this>]
Spec refs: [§X.Y, AC-NNN]
Key files: [<the files this unit creates or changes>]
Spend: <none|paid>

Description:
- <one sentence goal>

Core Requirements:
- <bullet list from spec>

Deliverables:
- <concrete files or components to create>
```

### Agent S3: Test Strategy Definition

**Responsibility:** Define comprehensive test expectations for each unit.

**Tasks:**
- Map test types to each unit based on IMPLEMENTATION_TYPE
- Define test coverage requirements
- Identify test data and fixtures needed
- Define mocking strategy
- Identify edge cases and error scenarios

**Output Format:**
```markdown
### Synthesis Agent 3: Test Strategy

Test Plans by Unit:

Unit ID: <matches S2 output>

Test Types Required:
- <based on IMPLEMENTATION_TYPE, list applicable types>

Test Coverage:
- Happy Path: <scenarios>
- Edge Cases: <scenarios>
- Error Cases: <scenarios>
- Performance: <if applicable>
- Security: <if applicable>

Test Data Requirements:
- Fixtures: <what test data needed>
- Mocks: <what to mock, why>
- Stubs: <what external dependencies>

Acceptance Criteria (Testable):
- [ ] <specific, measurable criteria>
- [ ] <specific, measurable criteria>
```

### Agent S4: Dependency and Risk Analysis

**Responsibility:** Analyze dependencies and risks across all units.

**Tasks:**
- Build complete dependency graph
- Identify critical path units
- Identify parallel work opportunities
- Flag integration points and risks
- Recommend implementation order

**Output Format:**
```markdown
### Synthesis Agent 4: Dependency & Risk Analysis

Dependency Graph:
- <Unit ID> → depends on → [<Unit IDs>]
- <visualize critical path>

Critical Path Units:
- <Units that block the most other units>

Parallel Work Streams:
- Stream 1: [<units that can be done in parallel>]
- Stream 2: [<units that can be done in parallel>]

Integration Points (High Risk):
- Point: <where units integrate>
  Units Involved: [<unit IDs>]
  Risk: <what could go wrong>
  Mitigation: <how to reduce risk>

Recommended Implementation Order:
1. <Unit ID> - <reason>
2. <Unit ID> - <reason>
```

### Agent S5: Traceability Matrix

**Responsibility:** Ensure complete traceability from spec to implementation.

**Tasks:**
- Map every spec section to at least one unit
- Map every unit to test types
- Identify coverage gaps
- Generate traceability report

**Output Format:**
```markdown
### Synthesis Agent 5: Traceability Matrix

Spec Section → Units Mapping:
- Spec X.Y: → [Unit IDs]
- <ensure no spec section is unmapped>

Unit → Test Type Mapping:
- Unit ID: → [Test Types]
- <ensure every unit has appropriate tests>

Coverage Validation:
- Total Spec Sections: <count>
- Mapped Sections: <count>
- Unmapped Sections: [<list if any>]
- Coverage: <percentage>

Reverse Traceability:
- Unit ID → Spec Sections → Test Types
```

---

## Phase 3: Write Implementation Plan (Main Agent Only)

**Task:** Merge all synthesis outputs into a single implementation plan document.

**Output Location:** `OUTPUT_PATH` (from input parameters)

### Plan Document Structure

```markdown
# Implementation Plan: <Name>

**Source Specification:** SPEC_FILE
**Implementation Type:** IMPLEMENTATION_TYPE
**Status:** Draft | In Progress | Complete
**Created:** YYYY-MM-DD
**Last Updated:** YYYY-MM-DD

---

## Table of Contents

1. [Overview](#overview)
2. [Scope and Objectives](#scope-and-objectives)
3. [Implementation Units](#implementation-units)
4. [Dependency Graph](#dependency-graph)
5. [Test Strategy](#test-strategy)
6. [Assumptions and Risks](#assumptions-and-risks)
7. [Traceability Matrix](#traceability-matrix)
8. [Implementation Order](#implementation-order)
9. [Parallel Bundles](#parallel-bundles)
```

### Unit Template (Use Exactly)

```markdown
## Unit: <name> (ID: <id>)

**Priority:** P0-Critical | P1-High | P2-Medium | P3-Low
**Type:** <component|service|api|migration|config|test>
**Size Estimate:** Small | Medium | Large
**Spend:** none | paid
**Spec refs:** §<section>, AC-<id> (the spec sections and criteria this unit implements)
**Key files:** `<path>`, `<path>` (at most 8; the harness rejects bigger units)

### Goal
<One sentence describing what this unit achieves>

### Deliverables (files)
- `<path>` — <create|extend>: <what changes>

### Requirements
- <Core requirements from spec - use section pointers>

### Dependencies
- **Depends On:** [Unit IDs that must be completed first]
- **Enables:** [Unit IDs that depend on this]
- **External Dependencies:** [Libraries, APIs, infrastructure]

### Acceptance Criteria
- [ ] WHEN <event or input>, THE SYSTEM SHALL <observable response> (one testable behaviour per line)

### Test Plan

**Test Types:** <Applicable types>

**Test Data:**
- Fixtures: <what test data needed>
- Mocks: <what to mock>

**Edge Cases:**
- <Boundary conditions>

### Definition of Done
- [ ] Acceptance criteria met
- [ ] All tests implemented and passing
- [ ] No skipped or flaky tests

### Risks and Mitigations
- **Risk:** <potential issue>
  - **Mitigation:** <how to address>

### Rollback Plan
<!-- Required for type: migration or api. Write "N/A" for all other types. -->
- **If this unit must be reverted:** <exact steps to undo — migration down command, API version deprecation path, data restore procedure>
```

Write a `## Parallel Bundles` section after `## Implementation Order`. Group units by dependency layer — units in the same layer have no dependencies on each other and can be implemented concurrently. Format:

```markdown
## Parallel Bundles

### Layer 1 (no dependencies)
- U001, U003, U005

### Layer 2 (depends only on Layer 1)
- U002, U004

### Layer 3 (depends on Layer 2)
- U006
```

The build loop uses this to identify which units can safely run in parallel.

---

## Phase 4: Foundation Units (Conditional)

**Apply only if:** EXISTING_CODE = false (greenfield implementation)

Based on IMPLEMENTATION_TYPE, ensure the plan includes foundational units for tech stack setup, testing harness, and golden-path E2E coverage.

---

## Phase 5: Spec Change Policy

Modify SPEC_FILE ONLY if Synthesis Agent 1 identifies a **true blocker** (missing, contradictory, or ambiguous requirement that prevents implementation). Changes must be minimal, justified, and documented.

---

## Phase 6: Quality Validation (Main Agent)

- [ ] Every spec section maps to at least one unit
- [ ] Every unit has acceptance criteria and test expectations
- [ ] Unit IDs are unique and sequential
- [ ] Dependencies are valid (no circular deps)
- [ ] Units are atomic and PR-sized (1-3 days each)

---

## Output Constraints

**Do NOT:** implement code, paste spec text, use prose, make undocumented assumptions.
**Do:** use section pointers, keep units atomic, plan tests as first-class work, ensure full spec coverage.

---

**END OF PROMPT**
