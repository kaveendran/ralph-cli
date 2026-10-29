#!/usr/bin/env bash
# ralph — Ralph Loop CLI
# Usage: ralph <command> [options]
#
# Commands:
#   init          Scaffold the Ralph pattern into a project
#   add-cr        Scaffold a new change request
#   start         Run the Ralph Loop (plan or build mode)
#   critique      Council-of-experts plan review before build mode
#   refine-plan   Bounded critique → apply-fixes loop on the plan
#   status        Show CR progress and what to do next
#   watch         Live structured tail of the running loop
#   retro         Post-build retrospective + release-notes draft
#   deploy        Deployment workflow (scaffold | critique | refine)
#   help          Show this help

# Resolve the package dir, following symlinks (e.g. /usr/local/bin/ralph).
# Kept bash-3.2 compatible for stock macOS bash.
RALPH_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$RALPH_SOURCE" ]; do
  RALPH_LINK_DIR="$(cd -P "$(dirname "$RALPH_SOURCE")" && pwd)"
  RALPH_SOURCE="$(readlink "$RALPH_SOURCE")"
  [ "${RALPH_SOURCE#/}" = "$RALPH_SOURCE" ] && RALPH_SOURCE="$RALPH_LINK_DIR/$RALPH_SOURCE"
done
RALPH_DIR="$(cd -P "$(dirname "$RALPH_SOURCE")" && pwd)"

usage() {
  echo ""
  echo "ralph — Ralph Loop CLI"
  echo ""
  echo "Usage: ralph <command> [options]"
  echo ""
  echo "Commands:"
  echo "  init       Scaffold the Ralph Loop pattern into a project directory."
  echo "             Options: --project-dir PATH, --auto, --manual"
  echo ""
  echo "  add-cr     Scaffold a new change request under development/crN/."
  echo "             Options: --number N, --title TEXT, --type TYPE,"
  echo "                      --prior-cr N, --spec-file PATH, --notes TEXT"
  echo "             Types: backend | frontend | backend+frontend | feature | api | database | refactor"
  echo ""
  echo "  status     Show where you are in the CR workflow and what to do next."
  echo "             Options: --cr N  (defaults to highest-numbered CR)"
  echo ""
  echo "  start      Run the Ralph Loop."
  echo "             Options: --mode plan|build (default: build)"
  echo "                      --cr N           (CR number — auto-resolves prompt path)"
  echo "                      --prompt PATH    (explicit prompt file override)"
  echo "                      --max N          (max iterations; default 1 for plan, 0 = unlimited for build)"
  echo "                      --model opus|sonnet (default: opus for plan, sonnet for build)"
  echo "                      --max-turns N, --max-budget USD, --compact-window TOKENS"
  echo "                                       (per-iteration caps; defaults 200 turns, \$30, 200000 for build)"
  echo "                      --dry-run        (show the next unit, prompt size and command; no Claude call)"
  echo "                      --push           (push after each iteration; off by default)"
  echo "                      --no-hermetic    (use your full personal Claude setup)"
  echo ""
  echo "  critique   Council-of-experts plan review — run after plan, before build."
  echo "             Spawns 8 parallel expert subagents (architect, tester, risk analyst,"
  echo "             domain expert, infra, security, performance, docs), synthesizes"
  echo "             findings into one prioritized report saved under development/crN/critique/."
  echo "             Options: --cr N  (defaults to highest-numbered CR with a plan)"
  echo ""
  echo "  refine-plan   Bounded critique → apply-fixes loop on the plan."
  echo "                Each iteration: re-critique, propose plan edits, ask human gate."
  echo "                Options: --cr N, --max N (default 3), --gate human|auto,"
  echo "                         --scope critical,important (which severities to apply)"
  echo ""
  echo "  watch      Live structured tail of the running loop."
  echo "             Reads log/index.jsonl + the latest iteration log, formats events."
  echo "             Options: --index-only, --log-only, --no-follow"
  echo ""
  echo "  retro      Post-build retrospective: plan-vs-reality, blocker patterns,"
  echo "             test-coverage gaps, plus draft RELEASE_NOTES.md and PR description."
  echo "             Options: --cr N, --no-files"
  echo ""
  echo "  deploy     Deployment workflow. Three subcommands:"
  echo "             - ralph deploy scaffold --cr N    # derive deploy artifacts from plan"
  echo "             - ralph deploy critique --cr N    # 6-expert council + peer-review"
  echo "             - ralph deploy refine   --cr N    # bounded apply-fixes loop"
  echo ""
  echo "Typical workflow:"
  echo "  1. ralph init                           # bootstrap Ralph in this project"
  echo "  2. ralph add-cr --number 1 --type backend"
  echo "  3. ralph status --cr 1                  # shows: write your spec first"
  echo "  4. edit development/cr1/spec/spec.md"
  echo "  5. ralph start --mode plan  --cr 1      # Claude generates implementation plan"
  echo "  6. ralph critique --cr 1                # 4 experts review the plan"
  echo "  7. edit plan if needed, then:"
  echo "  8. ralph start --mode build --cr 1      # Claude implements unit by unit"
  echo "  9. ralph status --cr 1                  # shows: N/M units complete"
  echo ""
}

COMMAND="${1:-}"
shift 2>/dev/null || true

case "$COMMAND" in
  init)        bash "$RALPH_DIR/commands/init.sh"        "$@" ;;
  add-cr)      bash "$RALPH_DIR/commands/add-cr.sh"      "$@" ;;
  status)      bash "$RALPH_DIR/commands/status.sh"      "$@" ;;
  start)       bash "$RALPH_DIR/commands/start.sh"       "$@" ;;
  critique)    bash "$RALPH_DIR/commands/critique.sh"    "$@" ;;
  refine-plan) bash "$RALPH_DIR/commands/refine-plan.sh" "$@" ;;
  watch)       bash "$RALPH_DIR/commands/watch.sh"       "$@" ;;
  retro)       bash "$RALPH_DIR/commands/retro.sh"       "$@" ;;
  deploy)      bash "$RALPH_DIR/commands/deploy.sh"      "$@" ;;
  help|--help|-h|"") usage ;;
  *)
    echo "Unknown command: $COMMAND"
    usage
    exit 1
    ;;
esac
