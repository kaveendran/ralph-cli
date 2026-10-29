#!/usr/bin/env bash
# ralph deploy — three-step deployment workflow.
#
# Subcommands:
#   scaffold  — derive deploy-plan / runbook / rollback / verify skeletons
#               from the plan. Fills structural sections, leaves environment
#               specifics as explicit <TODO: ...> markers for the human.
#   critique  — 6-expert council (migration, feature-flag, rollout, observability,
#               rollback, compliance) + peer-review pass + synthesizer.
#   refine    — bounded critique → apply-fixes loop with human gate (same
#               shape as `ralph refine-plan`, targeting deploy-plan.md).
#
# Design intent: structural automation, never content fabrication. The LLM
# fills what's derivable from the plan; the human fills environment specifics.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

SUB="${1:-help}"
shift 2>/dev/null || true

# ── Shared helpers ────────────────────────────────────────────────────────────

# resolve_cr <given>  →  echoes CR number (latest with a plan if blank)
resolve_cr() {
  local given="${1:-}"
  if [[ -n "$given" ]]; then echo "$given"; return; fi
  local latest=""
  for d in development/cr*/; do
    n="${d#development/cr}"; n="${n%/}"
    [[ "$n" =~ ^[0-9]+$ ]] || continue
    for f in "${d}plan/"*"-implementation-plan.md"; do
      [[ -f "$f" ]] || continue
      if [[ -z "$latest" || "$n" -gt "$latest" ]]; then latest="$n"; fi
    done
  done
  echo "$latest"
}

# resolve_plan_file <cr_dir>  →  echoes the plan path (or empty)
resolve_plan_file() {
  local cr_dir="$1"
  for f in "${cr_dir}/plan/"*"-implementation-plan.md"; do
    [[ -f "$f" ]] && echo "$f" && return
  done
}

require_claude() {
  command -v claude &>/dev/null || { echo "Error: 'claude' CLI not found in PATH."; exit 1; }
}

# ──────────────────────────────────────────────────────────────────────────────
# deploy scaffold
# ──────────────────────────────────────────────────────────────────────────────
cmd_scaffold() {
  local CR_NUMBER=""
  local FORCE=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cr)    CR_NUMBER="$2"; shift 2 ;;
      --force) FORCE=1;        shift   ;;
      *) echo "Unknown option: $1"; exit 1 ;;
    esac
  done

  CR_NUMBER="$(resolve_cr "$CR_NUMBER")"
  [[ -z "$CR_NUMBER" ]] && { echo "Error: no CR with a plan found. Use --cr N."; exit 1; }

  local CR_DIR="development/cr${CR_NUMBER}"
  local CR_NAME="CR${CR_NUMBER}"
  local PLAN_FILE; PLAN_FILE="$(resolve_plan_file "$CR_DIR")"
  [[ -z "$PLAN_FILE" ]] && { echo "Error: no plan in ${CR_DIR}/plan/."; exit 1; }

  local PROGRESS_FILE="${CR_DIR}/progress/implementation-progress.md"
  local SPEC_FILE="${CR_DIR}/spec/spec.md"
  local DEPLOY_DIR="${CR_DIR}/deploy"
  mkdir -p "$DEPLOY_DIR"

  local TARGETS=(deploy-plan.md runbook.md rollback.md verify.md)
  if [[ $FORCE -eq 0 ]]; then
    for t in "${TARGETS[@]}"; do
      if [[ -f "${DEPLOY_DIR}/${t}" ]]; then
        tui_log_skip "${DEPLOY_DIR}/${t} exists — use --force to regenerate"
        return 0
      fi
    done
  fi

  require_claude
  tui_header "deploy scaffold — $CR_NAME" "deriving structural deploy artifacts from plan"

  local PLAN_CONTENT PROGRESS_CONTENT SPEC_EXCERPT
  PLAN_CONTENT=$(cat "$PLAN_FILE")
  PROGRESS_CONTENT=$(cat "$PROGRESS_FILE" 2>/dev/null || echo "(no progress file)")
  SPEC_EXCERPT=$(grep -A2 '^## .* Acceptance' "$SPEC_FILE" 2>/dev/null || cat "$SPEC_FILE" 2>/dev/null || echo "")

  local TMP_DIR; TMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TMP_DIR"' RETURN

  local DP_OUT="$TMP_DIR/deploy-plan.md"
  local RB_OUT="$TMP_DIR/runbook.md"
  local RO_OUT="$TMP_DIR/rollback.md"
  local VF_OUT="$TMP_DIR/verify.md"

  local SCAFFOLD_RULES='SCAFFOLD RULES — read carefully:
- DO fill in everything that is derivable from the plan: unit order, unit types, per-unit deploy intent, rollback procedure text (pull verbatim from each unit'\''s "### Rollback Plan" section).
- DO NOT invent environment specifics. The following MUST appear as explicit `<TODO: ...>` markers for the human to fill:
    * Cluster / region names
    * Secrets manager paths
    * Service URLs / endpoints
    * Monitoring dashboard URLs
    * Feature-flag service identifiers
    * On-call rotation contact / escalation path
    * Maintenance windows / quiet hours
    * Traffic percentages for canary stages
    * Database connection strings
    * Specific Kubernetes namespaces / contexts
- If a unit has Type: migration, its deploy step MUST flag online-vs-offline, lock duration, and reversibility — leave the answer as `<TODO: ...>` if not derivable.
- If a unit has Type: api with breaking changes, the deploy step MUST reference a versioning / deprecation path.
- If a unit introduces a feature flag, the deploy step MUST list default state + kill-switch path as `<TODO: ...>`.
- Preserve unit IDs everywhere. Cross-reference by ID, not by name.
- No prose padding. Bullet lists, tables, and headed sections only.'

  tui_section "Generating four scaffold artifacts in parallel"

  # ── deploy-plan.md ──
  printf 'You are generating a DEPLOYMENT PLAN scaffold from an implementation plan.\n\n%s\n\nOutput a single markdown file with these exact top-level sections (in this order):\n\n# Deployment Plan: %s\n\n## Overview\n(2-4 bullets summarizing the deploy from the plan)\n\n## Environment\n(table of env-specific TODOs the human MUST fill — cluster, region, namespace, secrets path, dashboards, flag service, on-call. Leave every value as `<TODO: ...>`.)\n\n## Pre-deploy Gates\n(numbered list — checks that MUST be green before pressing go. Derive structural items from the plan; mark env-specific ones as `<TODO: ...>`.)\n\n## Per-unit Deploy Steps\n(One subsection per unit in the order the plan recommends. For each: ### UNIT_ID — Name\nType:, Risk:, Action:, Verification:, Notes:. Pull risk and rollback hints from the unit'\''s plan body.)\n\n## Rollout Strategy\n(canary / blue-green / rolling — leave traffic %% and dwell time as `<TODO: ...>`)\n\n## Observability\n(metrics, logs, alerts that MUST exist before deploy; mark env-specific paths as `<TODO: ...>`)\n\n## Compliance & Audit\n(change ticket, approvers, audit trail destination — env-specific as `<TODO: ...>`)\n\n## Open Questions\n(unanswered items extracted from the plan'\''s Risks or Assumptions sections)\n\n---\nPLAN:\n%s\n\n---\nPROGRESS:\n%s\n' \
    "$SCAFFOLD_RULES" "$CR_NAME" "$PLAN_CONTENT" "$PROGRESS_CONTENT" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$DP_OUT" 2>/dev/null &
  local DP_PID=$!

  # ── runbook.md ──
  printf 'You are generating a DEPLOYMENT RUNBOOK scaffold from an implementation plan.\n\n%s\n\nA runbook is a numbered, ordered checklist a human follows during deploy. Output:\n\n# Runbook: %s\n\n## 0. Pre-flight\n(numbered steps — checks before touching anything. Env-specific commands as `<TODO: ...>`.)\n\n## 1. Deploy Steps (in order)\n(numbered. One step per unit OR per logical phase. Each step:\n  - **Action:** what to run / click\n  - **Expected:** observable success signal\n  - **If wrong:** branch to rollback.md section X\n  - **Owner:** `<TODO: role/person>`\nUse the plan'\''s Implementation Order as the sequence. Migration units come BEFORE the API/frontend units that depend on them.)\n\n## 2. Smoke & Verify\n(reference verify.md)\n\n## 3. Post-deploy\n(numbered — monitoring window, cleanup, announce)\n\n---\nPLAN:\n%s\n' \
    "$SCAFFOLD_RULES" "$CR_NAME" "$PLAN_CONTENT" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$RB_OUT" 2>/dev/null &
  local RB_PID=$!

  # ── rollback.md ──
  printf 'You are generating a ROLLBACK PROCEDURE document from an implementation plan.\n\n%s\n\nFor EVERY unit in the plan, locate its `### Rollback Plan` section and reproduce it (verbatim where possible) under a per-unit heading. If a unit has no Rollback Plan section in the plan, write `**Rollback for UNIT_ID:** <TODO: missing in plan — author must add>`.\n\nOutput:\n\n# Rollback Procedures: %s\n\n## Decision Tree\n(when to roll back vs roll forward — 4-6 bullets, generic)\n\n## Point of No Return\n(list the moments in the deploy AFTER which rollback becomes destructive or data-losing. Derive from migration/api units in the plan.)\n\n## Per-unit Rollback Procedures\n(one subsection per unit, IN REVERSE deploy order — last deployed unit rolled back first. Heading: ### UNIT_ID — Name. Body: the unit'\''s Rollback Plan content, plus `**Verify rollback:**` line with `<TODO: smoke command>`.)\n\n## Full-stack Rollback\n(catastrophic case — order of operations to revert everything. Mark env-specific commands `<TODO: ...>`.)\n\n---\nPLAN:\n%s\n' \
    "$SCAFFOLD_RULES" "$CR_NAME" "$PLAN_CONTENT" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$RO_OUT" 2>/dev/null &
  local RO_PID=$!

  # ── verify.md ──
  printf 'You are generating a POST-DEPLOY VERIFICATION checklist from an implementation plan and the source spec'\''s acceptance criteria.\n\n%s\n\nFor each top-level acceptance criterion from the spec, map it to one or more concrete observable checks. Each check is either:\n  - a curl / API call (mark the host as `<TODO: ...>`)\n  - a UI flow (page URL `<TODO: ...>`, expected element / behavior)\n  - a metric check (metric name, expected range, dashboard URL `<TODO: ...>`)\n  - a log assertion\n\nOutput:\n\n# Verification: %s\n\n## Acceptance Criteria Coverage\n(numbered. For each AC: AC text, mapped check(s), pass/fail criterion.)\n\n## Smoke Tests (run first)\n(5-8 minimal checks — basic auth, key endpoints, critical UI page)\n\n## Deep Verification (run within 1 hour)\n(broader functional checks against the spec)\n\n## Monitoring Watch (run for 24 hours)\n(metrics + alerts to watch, with expected baselines `<TODO: ...>`)\n\n---\nSPEC EXCERPT (acceptance criteria):\n%s\n\n---\nPLAN:\n%s\n' \
    "$SCAFFOLD_RULES" "$CR_NAME" "$SPEC_EXCERPT" "$PLAN_CONTENT" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$VF_OUT" 2>/dev/null &
  local VF_PID=$!

  tui_wait_pids "Scaffold agents running..." "$DP_PID" "$RB_PID" "$RO_PID" "$VF_PID"
  wait "$DP_PID" "$RB_PID" "$RO_PID" "$VF_PID" 2>/dev/null || true

  local moved=0
  for pair in \
    "$DP_OUT:${DEPLOY_DIR}/deploy-plan.md" \
    "$RB_OUT:${DEPLOY_DIR}/runbook.md" \
    "$RO_OUT:${DEPLOY_DIR}/rollback.md" \
    "$VF_OUT:${DEPLOY_DIR}/verify.md"; do
    local src="${pair%%:*}" dst="${pair##*:}"
    if [[ -s "$src" ]]; then
      mv "$src" "$dst"
      tui_log_create "$dst"
      moved=$((moved + 1))
    else
      tui_log_warn "$dst — generator returned empty, not written"
    fi
  done

  # Count TODOs the human still needs to fill
  local total_todos=0
  for f in "${TARGETS[@]}"; do
    [[ -f "${DEPLOY_DIR}/${f}" ]] || continue
    local n
    n=$(grep -cE '<TODO:[^>]*>' "${DEPLOY_DIR}/${f}" 2>/dev/null || echo 0)
    total_todos=$((total_todos + n))
  done

  echo ""
  tui_header "Scaffold complete" "$moved/4 files written  |  ${total_todos} <TODO> markers to fill"
  echo "Next steps:"
  echo "  1. Open each file in ${DEPLOY_DIR}/ and fill every <TODO: ...> marker."
  echo "  2. ralph deploy critique --cr ${CR_NUMBER}"
  echo "  3. ralph deploy refine   --cr ${CR_NUMBER}   (optional — bounded critique loop)"
  echo ""
}

# ──────────────────────────────────────────────────────────────────────────────
# deploy critique
# ──────────────────────────────────────────────────────────────────────────────
cmd_critique() {
  local CR_NUMBER=""
  local PEER_REVIEW=1   # cross-expert review step (Council Mode pattern)
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cr)             CR_NUMBER="$2"; shift 2 ;;
      --no-peer-review) PEER_REVIEW=0;  shift   ;;
      *) echo "Unknown option: $1"; exit 1 ;;
    esac
  done

  CR_NUMBER="$(resolve_cr "$CR_NUMBER")"
  [[ -z "$CR_NUMBER" ]] && { echo "Error: no CR with a plan found. Use --cr N."; exit 1; }

  local CR_DIR="development/cr${CR_NUMBER}"
  local CR_NAME="CR${CR_NUMBER}"
  local DEPLOY_DIR="${CR_DIR}/deploy"
  local DP="${DEPLOY_DIR}/deploy-plan.md"
  local RB="${DEPLOY_DIR}/runbook.md"
  local RO="${DEPLOY_DIR}/rollback.md"
  local VF="${DEPLOY_DIR}/verify.md"

  if [[ ! -f "$DP" ]]; then
    echo "Error: ${DP} not found. Run 'ralph deploy scaffold --cr ${CR_NUMBER}' first."
    exit 1
  fi

  # Warn about un-filled TODOs but proceed — critique should flag them too
  local todo_count
  todo_count=$(grep -cE '<TODO:[^>]*>' "$DP" "$RB" "$RO" "$VF" 2>/dev/null | awk -F: '{s+=$NF} END{print s}')
  todo_count=${todo_count:-0}
  if [[ "$todo_count" -gt 0 ]]; then
    tui_log_warn "${todo_count} unresolved <TODO> markers across deploy artifacts — critique will flag them"
  fi

  require_claude

  local PLAN_FILE; PLAN_FILE="$(resolve_plan_file "$CR_DIR")"
  local PROGRESS_FILE="${CR_DIR}/progress/implementation-progress.md"

  local DP_CONTENT RB_CONTENT RO_CONTENT VF_CONTENT PLAN_CONTENT PROGRESS_CONTENT
  DP_CONTENT=$(cat "$DP")
  RB_CONTENT=$(cat "$RB" 2>/dev/null || echo "(no runbook)")
  RO_CONTENT=$(cat "$RO" 2>/dev/null || echo "(no rollback)")
  VF_CONTENT=$(cat "$VF" 2>/dev/null || echo "(no verify)")
  PLAN_CONTENT=$(cat "$PLAN_FILE" 2>/dev/null || echo "(no plan)")
  PROGRESS_CONTENT=$(cat "$PROGRESS_FILE" 2>/dev/null || echo "(no progress)")

  tui_header "deploy critique — $CR_NAME" "6 experts + peer-review + synthesizer"

  local TMP_DIR; TMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TMP_DIR"' RETURN

  local MIG_OUT="$TMP_DIR/migration.txt"
  local FF_OUT="$TMP_DIR/feature-flag.txt"
  local RLT_OUT="$TMP_DIR/rollout.txt"
  local OBS_OUT="$TMP_DIR/observability.txt"
  local ROL_OUT="$TMP_DIR/rollback-expert.txt"
  local COMP_OUT="$TMP_DIR/compliance.txt"

  local BUNDLE='--- DEPLOY PLAN ---
'"$DP_CONTENT"'

--- RUNBOOK ---
'"$RB_CONTENT"'

--- ROLLBACK ---
'"$RO_CONTENT"'

--- VERIFY ---
'"$VF_CONTENT"'

--- SOURCE PLAN (for cross-reference) ---
'"$PLAN_CONTENT"'

--- PROGRESS (for completed-units context) ---
'"$PROGRESS_CONTENT"

  tui_section "Phase 1: 6 experts in parallel"

  printf 'You are a senior database migration engineer auditing a deploy plan against the implementation it ships.\n\nFocus only on migration safety. For every unit with Type: migration in the source plan, verify the deploy plan addresses:\n1. Ordering — migrations applied BEFORE the API/frontend units that depend on them.\n2. Online vs offline — long table changes flagged for off-peak window or use of online-migration tool.\n3. Lock duration — any ACCESS EXCLUSIVE / table-rewrite operation called out with expected duration.\n4. Reversibility — every migration has a verified down-migration in rollback.md.\n5. Data-loss windows — moments where rollback becomes destructive.\n6. Backfill strategy — when NOT NULL added with default to a large table, is backfill batched?\n7. Index creation — CONCURRENTLY for large-table indexes.\n\nOutput:\n## Migration Expert\n\n### Critical issues\n(list; cite unit IDs)\n\n### Important issues\n(list)\n\n### Suggestions\n(list)\n\n### Verdict\n(1 line: SAFE | UNSAFE — and why)\n\nTag each finding `[origin:migration]` at end.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$MIG_OUT" 2>/dev/null &
  local MIG_PID=$!

  printf 'You are a senior platform engineer specializing in feature flags and progressive delivery.\n\nFocus only on feature-flag and gating safety. For every unit in the source plan that touches user-visible behavior, verify:\n1. Flag presence — is the change gated behind a flag, or shipped on?\n2. Default state — flag defaults to OFF on new envs?\n3. Kill switch — how to disable in <60s without redeploy?\n4. Flag ownership — owner / approver named in plan?\n5. Flag cleanup — sunset date or condition stated?\n6. Per-tenant overrides — if multi-tenant, scoping rules clear?\n7. Coupled flags — interactions between flags considered?\n\nOutput:\n## Feature-Flag Expert\n\n### Critical issues\n(list; cite unit IDs)\n\n### Important issues\n(list)\n\n### Suggestions\n(list)\n\n### Verdict\n(SAFE | UNSAFE — why)\n\nTag findings `[origin:feature-flag]`.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$FF_OUT" 2>/dev/null &
  local FF_PID=$!

  printf 'You are a senior release engineer specializing in canary / blue-green / rolling deployments.\n\nFocus only on rollout safety. Verify:\n1. Rollout strategy declared and matches risk level of the change.\n2. Canary stages have explicit traffic %% and dwell time (or marked <TODO> — flag).\n3. Halt criteria for each canary stage (what metric / SLO triggers stop?).\n4. Automatic rollback triggers — are any defined, or is rollback purely manual?\n5. Blast radius — for breaking API changes, what client populations are affected per stage?\n6. Dependency order between services — is downstream ready before upstream cuts over?\n7. Maintenance windows — destructive ops scheduled outside peak traffic?\n\nOutput:\n## Rollout Expert\n\n### Critical / Important / Suggestions / Verdict\n(same format as above)\n\nTag findings `[origin:rollout]`.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$RLT_OUT" 2>/dev/null &
  local RLT_PID=$!

  printf 'You are a senior SRE focused on observability gates for deploys.\n\nFocus only on whether the right monitoring is in place BEFORE deploy. Verify:\n1. Metrics that must exist before deploy are present in observability.md (latency, error rate, throughput, business KPI per shipped feature).\n2. Alerts wired to the SLO/error budget — not just dashboards.\n3. Log search queries documented for each unit'\''s failure mode.\n4. Dashboard URLs filled in (not <TODO>) for the metrics the runbook references.\n5. Tracing — distributed trace coverage for new request paths.\n6. Baseline known — what does "normal" look like for the metrics that gate this deploy?\n7. Synthetic checks — for new user-facing endpoints, is there a synthetic probe?\n\nOutput:\n## Observability Expert\n\n### Critical / Important / Suggestions / Verdict\n(same format)\n\nTag findings `[origin:observability]`.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$OBS_OUT" 2>/dev/null &
  local OBS_PID=$!

  printf 'You are a senior incident-response engineer auditing the rollback story.\n\nFocus only on rollback feasibility. Verify:\n1. Rollback in rollback.md exists for every shipped unit (no unit lacks a procedure).\n2. Reverse order — rollback proceeds last-deployed-first.\n3. Point of no return correctly identified (any moment the document marks, AND any the document missed).\n4. Verification of rollback — for each rollback step, is there a "verify-rolled-back" check?\n5. Data-loss windows clearly marked — moments where rollback drops user data must be EXPLICIT.\n6. Time-to-rollback — can the full rollback be executed in under 30 min by one on-call?\n7. Roll-forward fallback — if rollback is too risky, is the forward-fix path documented?\n\nOutput:\n## Rollback Expert\n\n### Critical / Important / Suggestions / Verdict\n(same format)\n\nTag findings `[origin:rollback]`.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$ROL_OUT" 2>/dev/null &
  local ROL_PID=$!

  printf 'You are a senior governance / compliance engineer auditing a deploy plan against 2026 regulatory baselines (EU AI Act, DSA, SOC 2, internal change-management).\n\nFocus only on audit-trail and approval gates. Verify:\n1. Change ticket — referenced in pre-deploy gates with explicit `<TODO: ...>` only if env-specific.\n2. Approvers — roles named (security review, DBA for migrations, on-call lead). If <TODO> for names, role must still be named.\n3. Audit log destination — for security-sensitive operations (auth, role changes, data export, schema changes), is the audit trail destination named?\n4. Reversibility documented — does the rollback path produce its own audit entry?\n5. Sensitive-data handling — units touching PII have a documented data-handling rationale in deploy-plan?\n6. Reproducibility — could a future auditor reconstruct what shipped (prompt version, model version, plan version, deploy timestamp)?\n7. Sign-off gates — is there a moment requiring explicit human sign-off before traffic is cut?\n\nOutput:\n## Compliance Expert\n\n### Critical / Important / Suggestions / Verdict\n(same format)\n\nTag findings `[origin:compliance]`.\n\n---\n%s\n' "$BUNDLE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$COMP_OUT" 2>/dev/null &
  local COMP_PID=$!

  tui_wait_pids "6 deploy experts reviewing..." "$MIG_PID" "$FF_PID" "$RLT_PID" "$OBS_PID" "$ROL_PID" "$COMP_PID"
  wait "$MIG_PID" "$FF_PID" "$RLT_PID" "$OBS_PID" "$ROL_PID" "$COMP_PID" 2>/dev/null || true

  local MIG_REVIEW FF_REVIEW RLT_REVIEW OBS_REVIEW ROL_REVIEW COMP_REVIEW
  MIG_REVIEW=$(cat "$MIG_OUT")
  FF_REVIEW=$(cat "$FF_OUT")
  RLT_REVIEW=$(cat "$RLT_OUT")
  OBS_REVIEW=$(cat "$OBS_OUT")
  ROL_REVIEW=$(cat "$ROL_OUT")
  COMP_REVIEW=$(cat "$COMP_OUT")

  # ── Phase 2: peer-review pass (Council Mode pattern) ────────────────────────
  local PR_OUT="$TMP_DIR/peer-review.txt"
  if [[ $PEER_REVIEW -eq 1 ]]; then
    tui_section "Phase 2: peer-review — agreements, disagreements, unique findings"
    printf 'You are coordinating a peer-review pass over six expert deploy reviews.\n\nProduce a short cross-review:\n1. AGREEMENTS — issues flagged by 2+ experts. List once; cite which experts agree.\n2. DISAGREEMENTS — places experts contradict each other (e.g., one says SAFE, another says UNSAFE on the same unit). Quote both positions.\n3. UNIQUE FINDINGS — high-confidence issues raised by only one expert (call out so the synthesizer does not under-weight them).\n4. CONFIDENCE — for each top finding, a one-word confidence label: HIGH | MEDIUM | LOW.\n\nKeep it to ~30 lines total. No prose padding. Use tagged citations like `[origin:migration]`.\n\n---\nMIGRATION:\n%s\n\n---\nFEATURE-FLAG:\n%s\n\n---\nROLLOUT:\n%s\n\n---\nOBSERVABILITY:\n%s\n\n---\nROLLBACK:\n%s\n\n---\nCOMPLIANCE:\n%s\n' \
      "$MIG_REVIEW" "$FF_REVIEW" "$RLT_REVIEW" "$OBS_REVIEW" "$ROL_REVIEW" "$COMP_REVIEW" \
      | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
        > "$PR_OUT" 2>/dev/null
  else
    printf '(peer-review skipped)\n' > "$PR_OUT"
  fi

  local PR_REVIEW; PR_REVIEW=$(cat "$PR_OUT")

  # ── Phase 3: synthesizer ────────────────────────────────────────────────────
  tui_section "Phase 3: synthesizing final deploy critique"

  local REPORT_DIR="${DEPLOY_DIR}/critique"
  mkdir -p "$REPORT_DIR"
  local REPORT_FILE="${REPORT_DIR}/$(date +%Y%m%d-%H%M%S)-report.md"

  printf 'You are the deploy-critique synthesizer. Six experts plus a peer-review pass have audited a deploy plan. Produce ONE prioritized action report.\n\nRules:\n- De-duplicate using the AGREEMENTS section from peer-review.\n- For DISAGREEMENTS, surface the contradiction explicitly so the human resolves it — do not pick a winner.\n- Treat any UNSAFE verdict as CRITICAL unless peer-review marks it LOW confidence.\n- Treat unresolved `<TODO:...>` markers in the deploy artifacts as IMPORTANT (the human must fill before deploy).\n- Every item must reference a unit ID, runbook step, or deploy-plan section AND end with the exact edit to make.\n\nOutput:\n\n# Deploy Critique Report — %s\n\n## Verdict\n(GO | GO-WITH-FIXES | NO-GO — one line. Then 2-3 sentences justifying.)\n\n## CRITICAL — fix before deploy\n(numbered list, or "None.")\n\n## IMPORTANT — fix before deploy\n(numbered list, or "None.")\n\n## SUGGESTED — consider before deploy\n(numbered list, or "None.")\n\n## Disagreements between experts\n(if peer-review surfaced any — quote both positions and recommend whose view to follow given the evidence in the plan, or recommend human adjudication)\n\n## Deploy health\n(3-5 sentences: overall readiness, headline risks, headline strengths.)\n\n---\nPEER-REVIEW:\n%s\n\n---\nMIGRATION:\n%s\n\n---\nFEATURE-FLAG:\n%s\n\n---\nROLLOUT:\n%s\n\n---\nOBSERVABILITY:\n%s\n\n---\nROLLBACK:\n%s\n\n---\nCOMPLIANCE:\n%s\n' \
    "$CR_NAME" \
    "$PR_REVIEW" \
    "$MIG_REVIEW" "$FF_REVIEW" "$RLT_REVIEW" "$OBS_REVIEW" "$ROL_REVIEW" "$COMP_REVIEW" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
    | tee "$REPORT_FILE"

  echo ""
  tui_log_ok "Report saved: $REPORT_FILE"
  echo ""
  echo "Next:"
  echo "  - Address CRITICAL / IMPORTANT items in ${DEPLOY_DIR}/*.md"
  echo "  - ralph deploy refine --cr ${CR_NUMBER}    (bounded loop to auto-apply structural fixes)"
  echo ""
}

# ──────────────────────────────────────────────────────────────────────────────
# deploy refine — bounded critique → apply-fixes loop, human-gated
# ──────────────────────────────────────────────────────────────────────────────
cmd_refine() {
  local CR_NUMBER=""
  local MAX_ITERATIONS=3
  local GATE="human"
  local SCOPE="critical,important"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cr)    CR_NUMBER="$2";       shift 2 ;;
      --max)   MAX_ITERATIONS="$2";  shift 2 ;;
      --gate)  GATE="$2";            shift 2 ;;
      --scope) SCOPE="$2";           shift 2 ;;
      *) echo "Unknown option: $1"; exit 1 ;;
    esac
  done

  CR_NUMBER="$(resolve_cr "$CR_NUMBER")"
  [[ -z "$CR_NUMBER" ]] && { echo "Error: no CR with a plan found. Use --cr N."; exit 1; }

  local CR_DIR="development/cr${CR_NUMBER}"
  local CR_NAME="CR${CR_NUMBER}"
  local DEPLOY_DIR="${CR_DIR}/deploy"
  local DP="${DEPLOY_DIR}/deploy-plan.md"
  local CRITIQUE_DIR="${DEPLOY_DIR}/critique"
  local REVISIONS_DIR="${DEPLOY_DIR}/revisions"

  [[ ! -f "$DP" ]] && { echo "Error: $DP not found. Run 'ralph deploy scaffold' first."; exit 1; }
  mkdir -p "$CRITIQUE_DIR" "$REVISIONS_DIR"

  local V0="${REVISIONS_DIR}/v0-initial.md"
  [[ ! -f "$V0" ]] && cp "$DP" "$V0"

  tui_header "deploy refine — $CR_NAME" "deploy-plan: $DP  |  max: $MAX_ITERATIONS  |  gate: $GATE  |  scope: $SCOPE"

  require_claude

  latest_critique() { ls -t "$CRITIQUE_DIR"/*-report.md 2>/dev/null | head -1 || true; }

  count_severity() {
    local report="$1" sev="$2"
    awk -v sev="$sev" '
      BEGIN { in_section=0; count=0 }
      /^## / {
        if ($0 ~ sev) { in_section=1; next }
        else if (in_section) { in_section=0 }
      }
      in_section && /^[0-9]+\./ { count++ }
      END { print count }
    ' "$report"
  }

  local ITER=0 NOOPS=0
  while [[ $ITER -lt $MAX_ITERATIONS ]]; do
    ITER=$((ITER + 1))
    echo ""
    if tui_available; then
      gum style --foreground 99 --bold "  ── deploy refine iteration $ITER / $MAX_ITERATIONS ──"
    else
      echo "──── deploy refine iteration $ITER / $MAX_ITERATIONS ────"
    fi

    if [[ $ITER -eq 1 ]] && [[ -n "$(latest_critique)" ]]; then
      REPORT="$(latest_critique)"
      tui_log_ok "Using existing critique report: $REPORT"
    else
      tui_section "Running deploy critique"
      cmd_critique --cr "$CR_NUMBER" --no-peer-review > /dev/null
      REPORT="$(latest_critique)"
      [[ -z "$REPORT" ]] && { echo "Error: critique produced no report."; exit 1; }
    fi

    local CRIT_COUNT IMPT_COUNT
    CRIT_COUNT=$(count_severity "$REPORT" "CRITICAL")
    IMPT_COUNT=$(count_severity "$REPORT" "IMPORTANT")
    printf '  Severity counts: \033[31mCRITICAL=%s\033[0m  \033[33mIMPORTANT=%s\033[0m\n' \
      "$CRIT_COUNT" "$IMPT_COUNT"

    if [[ "${CRIT_COUNT:-0}" -eq 0 && "${IMPT_COUNT:-0}" -eq 0 ]]; then
      tui_log_ok "Deploy plan has converged — no CRITICAL or IMPORTANT items remain."
      break
    fi

    tui_section "Generating proposed deploy-plan edits"
    local CANDIDATE="${REVISIONS_DIR}/v${ITER}-candidate.md"
    local DP_BEFORE; DP_BEFORE=$(cat "$DP")
    local REPORT_CONTENT; REPORT_CONTENT=$(cat "$REPORT")

    printf 'You are revising a deploy-plan based on a critique report.\n\nRules:\n- Apply only items in scope: %s.\n- Make STRUCTURAL edits only. Examples of allowed edits: adding a missing pre-deploy gate, adding a missing per-unit step, fixing migration ordering, adding a missing rollback reference, naming an absent approver role, adding a missing observability hook.\n- DO NOT invent environment-specific content. If a critique item requires a value the deploy plan does not contain (cluster name, secret path, dashboard URL, on-call contact), leave a `<TODO: ...>` marker rather than guessing.\n- Preserve unit IDs and section ordering.\n- Output the COMPLETE revised deploy-plan. No preamble, no diff — just the full markdown file.\n\n---\nCRITIQUE REPORT:\n%s\n\n---\nCURRENT DEPLOY PLAN:\n%s\n' \
      "$SCOPE" "$REPORT_CONTENT" "$DP_BEFORE" \
      | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
        > "$CANDIDATE" 2>/dev/null

    if [[ ! -s "$CANDIDATE" ]]; then
      tui_log_warn "apply-fixes produced no output — stopping."
      break
    fi

    if cmp -s "$DP" "$CANDIDATE"; then
      NOOPS=$((NOOPS + 1))
      tui_log_warn "apply-fixes returned no changes (no-op iteration)."
      [[ $NOOPS -ge 1 ]] && { tui_log_ok "Stopping — apply-fixes no longer changing the deploy plan."; break; }
      continue
    else
      NOOPS=0
    fi

    echo ""
    tui_section "Proposed changes"
    if command -v diff &>/dev/null; then
      diff -u "$DP" "$CANDIDATE" | head -120 || true
      echo ""
      echo "  ... (truncated to first 120 diff lines — full candidate: $CANDIDATE)"
    else
      echo "  diff not available; candidate at $CANDIDATE"
    fi

    local ACTION="apply"
    if [[ "$GATE" == "human" ]]; then
      echo ""
      ACTION="$(tui_choose "Apply this revision?" \
        "apply  — accept and re-critique" \
        "skip   — reject, continue to next iteration" \
        "done   — accept and stop" \
        "quit   — reject and stop")"
      ACTION="${ACTION%% *}"
    fi

    case "$ACTION" in
      apply|done)
        cp "$CANDIDATE" "${REVISIONS_DIR}/v${ITER}.md"
        cp "$CANDIDATE" "$DP"
        tui_log_ok "Applied revision v${ITER} to $DP"
        [[ "$ACTION" == "done" ]] && break
        ;;
      skip) tui_log_skip "Skipped (candidate kept at $CANDIDATE)" ;;
      quit) tui_log_skip "Stopped by user. Candidate at $CANDIDATE."; break ;;
    esac
  done

  echo ""
  tui_header "deploy refine complete" "$ITER iteration(s)  |  revisions: $REVISIONS_DIR"
}

# ──────────────────────────────────────────────────────────────────────────────
# Help / dispatch
# ──────────────────────────────────────────────────────────────────────────────
cmd_help() {
  cat <<'EOF'

ralph deploy — three-step deployment workflow

Usage: ralph deploy <subcommand> [options]

Subcommands:
  scaffold   Derive deploy-plan / runbook / rollback / verify skeletons from
             the implementation plan. Structural sections are filled; every
             environment-specific value is left as an explicit <TODO: ...>
             marker for the human to fill.
             Options: --cr N, --force

  critique   6-expert council (migration, feature-flag, rollout, observability,
             rollback, compliance) + peer-review pass + synthesizer. Saves
             report to development/crN/deploy/critique/<ts>-report.md.
             Options: --cr N, --no-peer-review

  refine     Bounded critique -> apply-fixes loop on deploy-plan.md. Default
             max 3 iterations, human gate between each. Structural edits only;
             never invents environment-specific content.
             Options: --cr N, --max N (default 3), --gate human|auto,
                      --scope critical,important

Typical workflow:
  1. ralph deploy scaffold --cr 5
  2. (human fills <TODO: ...> markers in development/cr5/deploy/*.md)
  3. ralph deploy critique --cr 5
  4. ralph deploy refine   --cr 5    # optional
  5. (human executes runbook.md, presses go)

EOF
}

case "$SUB" in
  scaffold) cmd_scaffold "$@" ;;
  critique) cmd_critique "$@" ;;
  refine)   cmd_refine   "$@" ;;
  help|-h|--help|"") cmd_help ;;
  *) echo "Unknown deploy subcommand: $SUB"; cmd_help; exit 1 ;;
esac
