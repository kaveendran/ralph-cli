"""`ralph critique`, `ralph refine-plan`, `ralph retro`, `ralph deploy …`.

Each one is a handful of read-only Claude calls (see ralph/workflows/); it
reports what it wrote, what it cost, and where the logs are.
"""

from ralph import ui
from ralph.commands.common import load_project
from ralph.commands.scaffold import TYPES
from ralph.monitor.otel import otel_env
from ralph.paths import resolve_cr
from ralph.runner import compat
from ralph.runner.cli_backend import CliRunner
from ralph.workflows import critique, deploy, retro
from ralph.workflows.council import Council
from ralph.workflows.workspace import Workspace

SCOPES = "critical,important"


def register(subparsers) -> None:
    c = subparsers.add_parser("critique", help="8 experts review the plan before the build; one prioritized report")
    _common(c)
    c.add_argument("--type", choices=TYPES, help="the stack the domain expert reviews for (default: from dependencies)")
    c.set_defaults(handler=run_critique)

    r = subparsers.add_parser("refine-plan", help="critique → proposed plan edits → you approve each; repeat (bounded)")
    _common(r)
    _refine_options(r)
    r.add_argument("--type", choices=TYPES, help="as for critique")
    r.set_defaults(handler=run_refine_plan)

    t = subparsers.add_parser("retro", help="after the build: plan vs reality, blocker patterns, test gaps, release notes")
    _common(t)
    t.add_argument("--no-files", action="store_true", help="don't write RELEASE_NOTES.md and PR_DESCRIPTION.md")
    t.set_defaults(handler=run_retro)

    d = subparsers.add_parser("deploy", help="draft, critique and refine a CR's deploy plan, runbook, rollback and checks")
    actions = d.add_subparsers(title="actions", metavar="<action>", required=True)
    s = actions.add_parser("scaffold", help="draft the four deploy documents from the plan (environment details stay TODO)")
    _common(s)
    s.add_argument("--force", action="store_true", help="draft them again even if they exist")
    s.set_defaults(handler=run_deploy_scaffold)
    k = actions.add_parser("critique", help="6 deploy experts, a peer-review pass and a verdict")
    _common(k)
    k.add_argument("--no-peer-review", action="store_true", help="skip the cross-review step")
    k.set_defaults(handler=run_deploy_critique)
    f = actions.add_parser("refine", help="critique → proposed deploy-plan edits → you approve each; repeat (bounded)")
    _common(f)
    _refine_options(f)
    f.set_defaults(handler=run_deploy_refine)


def _common(parser) -> None:
    parser.add_argument("--cr", help="change request (default: the latest with units or a plan)")
    parser.add_argument("--model", help="model for every call (default: runner.model_build)")


def _refine_options(parser) -> None:
    parser.add_argument("--max", type=int, default=3, help="at most this many rounds (default 3)")
    parser.add_argument("--gate", choices=("human", "auto"), default="human",
                        help="auto applies every revision without asking (for CI)")
    parser.add_argument("--scope", default=SCOPES, help=f"severities to fix (default {SCOPES})")


def _setup(args, workflow: str) -> tuple[Workspace, Council]:
    project, config = load_project()
    ws = Workspace(project, config, project.cr(resolve_cr(project, args.cr)))
    compat.find_claude()
    developer = ws.git.run("config", "user.name", check=False).stdout.strip()
    council = Council(CliRunner(), ws.root, ws.run_dir(workflow), model=args.model or config.runner.model_build,
                      env=otel_env(config.telemetry, developer=developer, cr=ws.cr.name, loop=workflow))
    ui.header(f"{workflow.replace('-', ' ')} — {ws.cr.name}", f"model {council.model} · logs in {council.run_dir}")
    return ws, council


def _show(ws: Workspace, outcome, label: str) -> None:
    ui.info("")
    ui.info(outcome.text)
    ui.info("")
    ui.ok(f"{label} saved to {outcome.path.relative_to(ws.root)} · ${outcome.cost_usd:.2f}")


def run_critique(args) -> int:
    ws, council = _setup(args, "critique")
    outcome = critique.critique(ws, council, args.type or "")
    _show(ws, outcome, "Report")
    ui.info(f"  Fix the CRITICAL and IMPORTANT items in {ws.cr.plan.relative_to(ws.root)}, or let "
            f"'ralph refine-plan --cr {ws.cr.name}' propose the edits.")
    return 0


def run_refine_plan(args) -> int:
    ws, council = _setup(args, "refine-plan")
    critique.refine_plan(ws, council, args.max, args.gate, args.scope, args.type or "")
    if ws.store.exists():
        ui.info(f"  If the plan changed, update the units with 'ralph graph compile --cr {ws.cr.name}'.")
    return 0


def run_retro(args) -> int:
    ws, council = _setup(args, "retro")
    outcome, written = retro.retro(ws, council, write_files=not args.no_files)
    _show(ws, outcome, "Retrospective")
    for path in written:
        ui.ok(f"Draft: {path.relative_to(ws.root)}")
    return 0


def run_deploy_scaffold(args) -> int:
    ws, council = _setup(args, "deploy-scaffold")
    written, todos, cost = deploy.scaffold(ws, council, force=args.force)
    for path in written:
        ui.ok(f"Drafted {path.relative_to(ws.root)}")
    ui.info(f"\n  {len(written)}/4 documents · {todos} <TODO: …> markers for you to fill · ${cost:.2f}")
    ui.info(f"  Then: ralph deploy critique --cr {ws.cr.name}")
    return 0 if written else 1


def run_deploy_critique(args) -> int:
    ws, council = _setup(args, "deploy-critique")
    _show(ws, deploy.critique(ws, council, peer_review=not args.no_peer_review), "Report")
    return 0


def run_deploy_refine(args) -> int:
    ws, council = _setup(args, "deploy-refine")
    return deploy.refine_deploy(ws, council, args.max, args.gate, args.scope)
