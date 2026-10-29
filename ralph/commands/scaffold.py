"""`ralph init` and `ralph add-cr` — set up a project and its change requests.

A change request folder holds what the harness reads:
    development/<cr>/spec/spec.md              what to build (you write it)
    development/<cr>/plan/<cr>-implementation-plan.md   written by plan mode
    development/<cr>/units/                    unit state (the harness writes it)
    development/<cr>/prompts/spec-planning.md  the planning prompt
    development/<cr>/prompts/instructions.md   CR rules included in every unit pack
"""

import tempfile
from datetime import date
from pathlib import Path

from ralph import ui
from ralph.commands.migrate import GITIGNORE_LINES, detect_gates, render_config
from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.paths import ProjectPaths, find_project_root, normalise_cr
from ralph.prompts import TEMPLATES_DIR, render, render_template
from ralph.runner import compat
from ralph.runner.base import AgentCall
from ralph.runner.cli_backend import CliRunner
from ralph.runner.settings import agent_env, project_mcp_config

TYPES = ("backend+frontend", "backend", "frontend", "api", "database", "infrastructure", "refactor", "feature")
CONSTRAINTS = {
    "backend": "- Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest.\n- Alembic for all schema migrations.",
    "frontend": "- TypeScript 5+, React, Vite, Tailwind CSS, Vitest.\n- Colours via CSS variables or Tailwind utilities.",
    "backend+frontend": "- Backend: Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest.\n"
                        "- Frontend: TypeScript 5+, React, Vite, Tailwind CSS, Vitest.",
}


def register(subparsers) -> None:
    i = subparsers.add_parser("init", help="set up ralph in this project")
    i.add_argument("--name", help="project name (default: the folder name)")
    i.add_argument("--description", default="", help="one line about the project")
    i.add_argument("--type", choices=TYPES, help="implementation type")
    i.add_argument("--stack", default="", help="tech stack, used when Claude drafts the spec")
    i.add_argument("--brief", default="", help="goals and features; with this, Claude drafts the initial spec")
    i.add_argument("--test-infra", action="store_true",
                   help="also write quiet test scripts and an isolated docker-compose.test.yml (the gates use them)")
    i.set_defaults(handler=run_init)

    a = subparsers.add_parser("add-cr", help="add a change request")
    a.add_argument("--number", required=True, help="CR number, e.g. 5")
    a.add_argument("--title", default="", help="short title")
    a.add_argument("--type", choices=TYPES, default="feature", help="implementation type")
    a.add_argument("--spec-file", help="copy this file in as the spec")
    a.add_argument("--prior-cr", help="the CR this one builds on, e.g. 3 (linked from its spec and instructions)")
    a.add_argument("--notes", default="(none)", help="special notes included in every unit pack")
    a.set_defaults(handler=run_add_cr)


def create_cr(project: ProjectPaths, name: str, title: str, impl_type: str, notes: str,
              spec_source: Path | None = None, spec_text: str = "", prior: str = "") -> list[Path]:
    cr = project.cr(name)
    prior_spec = project.cr(prior).spec.relative_to(project.root) if prior else None
    created = []

    def write(path: Path, text: str) -> None:
        if path.exists():
            ui.info(f"  skip    {path.relative_to(project.root)} (exists)")
            return
        write_text_atomic(path, text)
        created.append(path)
        ui.info(f"  create  {path.relative_to(project.root)}")

    spec = spec_text or (spec_source.read_text(encoding="utf-8") if spec_source else
                         render_template("spec.md", CR_NAME=name, TITLE=title or name, DATE=date.today().isoformat(),
                                         BASELINE=f" It builds on {prior}: see `{prior_spec}`." if prior else ""))
    write(cr.spec, spec)
    write(cr.plan_prompt, render_template(
        "spec-planning.md", SPEC_PATH=str(cr.spec.relative_to(project.root)), IMPLEMENTATION_TYPE=impl_type,
        PLAN_PATH=str(cr.plan.relative_to(project.root)), EXISTING_CODE="true" if name != "initial" else "false",
        SPECIAL_NOTES=notes))
    write(cr.instructions, render_template(
        "instructions.md", CR_NAME=name, TECH_CONSTRAINTS=CONSTRAINTS.get(impl_type, "- (list them here)"),
        BASELINE=f"\n## Baseline\n\nThis CR builds on {prior}. Its spec, `{prior_spec}`, describes what already "
                 "exists; don't re-implement it.\n" if prior else "",
        SPECIAL_NOTES=notes))
    (cr.dir / "plan").mkdir(parents=True, exist_ok=True)
    return created


TEST_INFRA_DIR = TEMPLATES_DIR / "test-infra"
BACKEND_TYPES = ("backend+frontend", "backend", "api", "database")
FRONTEND_TYPES = ("backend+frontend", "frontend")


def write_test_infra(project: ProjectPaths, impl_type: str) -> list[str]:
    """Quiet, fail-fast test scripts and an isolated test stack. Existing files are kept."""
    files = {"scripts/test-unit.sh": "test-unit.sh"}
    if impl_type in BACKEND_TYPES or impl_type in FRONTEND_TYPES:
        files["docker-compose.test.yml"] = "docker-compose.test.yml"
    if impl_type in BACKEND_TYPES:
        files["scripts/test-api.sh"] = "test-api.sh"
    if impl_type in FRONTEND_TYPES:
        files.update({"scripts/test-e2e.sh": "test-e2e.sh", "e2e/playwright.config.ts": "playwright.config.ts",
                      ".ralph/mcp.json": "mcp.json"})       # the Playwright MCP server, for loop runs
    written = []
    for target, template in files.items():
        path = project.root / target
        if path.exists():
            ui.info(f"  skip    {target} (exists)")
            continue
        write_text_atomic(path, (TEST_INFRA_DIR / template).read_text(encoding="utf-8"))
        if path.suffix == ".sh":
            path.chmod(0o755)
        ui.info(f"  create  {target}")
        written.append(target)
    return written


def ensure_project_files(project: ProjectPaths, name: str, description: str) -> None:
    if not project.config.exists():
        write_text_atomic(project.config, render_config(detect_gates(project.root)))
        ui.info("  create  .ralph/config.toml")
    if not project.constitution.exists():
        write_text_atomic(project.constitution, render_template("constitution.md"))
        ui.info("  create  .ralph/constitution.md (the principles every unit is reviewed against)")
    claude_md = project.root / "CLAUDE.md"
    if not claude_md.exists():
        write_text_atomic(claude_md, render_template("project-map.md", PROJECT_NAME=name,
                                                     DESCRIPTION=description or "(one line about the project)"))
        ui.info("  create  CLAUDE.md")
    gitignore = project.root / ".gitignore"
    existing = read_text(gitignore)
    missing = [line for line in GITIGNORE_LINES if line not in existing.split()]
    if missing:
        write_text_atomic(gitignore, existing.rstrip("\n") + ("\n" if existing else "") +
                          "# ralph harness runtime files\n" + "\n".join(missing) + "\n")
        ui.info(f"  update  .gitignore (+ {', '.join(missing)})")


def draft_spec(root: Path, name: str, description: str, impl_type: str, stack: str, brief: str) -> str:
    """Ask Claude for a first spec (no tools: it only writes text)."""
    compat.find_claude()
    prompt = render("spec_generate.md", PROJECT_NAME=name, DESCRIPTION=description or "(none given)",
                    IMPLEMENTATION_TYPE=impl_type, TECH_STACK=stack or "to be decided", BRIEF=brief)
    with tempfile.TemporaryDirectory() as tmp:
        call = AgentCall(prompt=prompt, cwd=root, model="sonnet", log_path=Path(tmp) / "stream.jsonl",
                         max_turns=3, max_budget_usd=2.0, timeout_seconds=600, tools=[],
                         mcp_config=project_mcp_config(root, Path(tmp)), env=agent_env(root, 200_000, True))
        ui.info("  Drafting the spec with Claude (about 30 seconds)…")
        result = CliRunner().run(call)
    text = (result.result.get("result") or "").strip()
    if not text:
        raise RalphError(f"Claude didn't return a spec ({result.subtype or 'no result'}).",
                         "Run 'ralph init' without --brief and write the spec yourself.")
    return text + "\n"


def run_init(args) -> int:
    root = find_project_root()
    project = ProjectPaths(root)
    name = args.name or ui.ask("Project name", root.name)
    description = args.description or ui.ask("One-line description", "")
    impl_type = args.type or ui.choose("Implementation type", list(TYPES), default=len(TYPES) - 1)
    ui.header(f"init — {name}", str(root))
    if args.test_infra:
        written = write_test_infra(project, impl_type)
        if written and project.config.exists():
            ui.warn("The config already exists, so add the new scripts to [verify.gates] yourself.")
    ensure_project_files(project, name, description)
    spec_text = draft_spec(root, name, description, impl_type, args.stack, args.brief) if args.brief else ""
    create_cr(project, "initial", name, impl_type, "(none)", spec_text=spec_text)
    ui.info("")
    ui.info("  Next:")
    ui.info("    1. Review development/initial/spec/spec.md (number its sections: §1, §1.1, …)")
    ui.info("    2. Check the gates in .ralph/config.toml")
    ui.info("    3. git switch -c ralph/initial")
    ui.info("    4. ralph start --mode plan --cr initial")
    ui.info("    5. ralph start --cr initial")
    return 0


def run_add_cr(args) -> int:
    project = ProjectPaths(find_project_root())
    name = normalise_cr(args.number)
    if name == "initial":
        raise RalphError("Use a number for --number, e.g. 5.")
    spec_source = Path(args.spec_file) if args.spec_file else None
    if spec_source and not spec_source.exists():
        raise RalphError(f"--spec-file {spec_source} doesn't exist.")
    prior = normalise_cr(args.prior_cr) if args.prior_cr else ""
    if prior and not project.cr(prior).dir.is_dir():
        raise RalphError(f"--prior-cr {args.prior_cr}: there's no {project.cr(prior).dir.relative_to(project.root)}.")
    ui.header(f"add-cr — {name}", args.title)
    create_cr(project, name, args.title, args.type, args.notes, spec_source, prior=prior)
    ui.info("")
    ui.info(f"  Next: write {project.cr(name).spec.relative_to(project.root)}, then "
            f"'ralph start --mode plan --cr {name}'.")
    return 0
