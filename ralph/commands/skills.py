"""`ralph skills list | lint | refresh | init | eval` — the project's skills.

Skills live in `.claude/skills/<name>/SKILL.md`, committed with the project.
`init` drafts starter skills for your stack (you review them); `refresh`
flags skills and vendored docs written for versions you no longer use;
`eval` runs each skill's scenarios through Claude.
"""

from collections import Counter
from datetime import datetime

from ralph import ui
from ralph.commands.common import load_project
from ralph.errors import RalphError
from ralph.monitor.telemetry import read_records
from ralph.runner import compat
from ralph.runner.cli_backend import CliRunner
from ralph.skills.deps import installed_versions
from ralph.skills.evals import run_evals, scenarios
from ralph.skills.generate import generate_stack_skills
from ralph.skills.lint import lint_skills
from ralph.skills.refresh import stale_docs, stale_skills
from ralph.skills.stats import compare
from ralph.skills.registry import discover, skills_dir


def register(subparsers) -> None:
    p = subparsers.add_parser("skills", help="list, check, refresh, draft and evaluate the project's skills")
    actions = p.add_subparsers(title="actions", metavar="<action>", required=True)

    actions.add_parser("list", help="the skills, their size, and how often the loop used them").set_defaults(
        handler=run_list)

    lint = actions.add_parser("lint", help="check skills against Anthropic's skill authoring guidance")
    lint.add_argument("--strict", action="store_true", help="exit 1 on warnings too, not only errors")
    lint.set_defaults(handler=run_lint)

    actions.add_parser("refresh", help="flag skills and vendored docs written for other dependency versions"
                       ).set_defaults(handler=run_refresh)

    actions.add_parser("stats", help="did iterations that used each skill do better than those that didn't?"
                       ).set_defaults(handler=run_stats)

    init = actions.add_parser("init", help="draft starter skills for your stack with Claude, for you to review")
    init.add_argument("--stack", default="", help='e.g. "FastAPI + SQLAlchemy 2 + React 19"')
    init.add_argument("--max", type=int, default=3, help="at most this many skills (default 3)")
    init.set_defaults(handler=run_init)

    ev = actions.add_parser("eval", help="run each skill's scenarios in evals/*.json through Claude")
    ev.add_argument("name", nargs="?", help="only this skill")
    ev.add_argument("--model", default="haiku", help="model for the scenarios (default haiku)")
    ev.set_defaults(handler=run_eval)


def run_list(args) -> int:
    project, _ = load_project()
    skills = discover(project.root)
    if not skills:
        ui.info(f"No skills in {skills_dir(project.root)}. Draft some with 'ralph skills init --stack \"…\"'.")
        return 0
    used = Counter(name for record in read_records(project.iterations_log)
                   for name in (record.get("agent") or {}).get("skills_invoked", []))
    ui.header("skills", str(skills_dir(project.root)))
    for skill in skills:
        scope = f" · paths {', '.join(skill.paths)}" if skill.paths else ""
        ui.info(f"  {skill.name:<28} {skill.lines:>4} lines · used in {used[skill.name]} iteration(s){scope}")
        ui.info(f"      {skill.description[:110]}")
    return 0


def run_lint(args) -> int:
    project, config = load_project()
    findings = lint_skills(discover(project.root), config.memory.skill_lines)
    for finding in findings:
        report = ui.error if finding.severity == "error" else ui.warn
        report(f"{finding.path.relative_to(project.root) if finding.path.is_absolute() else finding.path}: "
               f"{finding.message}")
    if not findings:
        ui.ok("Skills follow the authoring guidance.")
    failing = [f for f in findings if args.strict or f.severity == "error"]
    return 1 if failing else 0


def run_refresh(args) -> int:
    project, _ = load_project()
    versions = installed_versions(project.root)
    stale = stale_skills(discover(project.root), versions) + stale_docs(project.root, versions)
    if not stale:
        ui.ok(f"Every skill and vendored doc matches the versions in use ({len(versions)} packages found).")
        return 0
    for item in stale:
        ui.warn(f"{item.item}: written for {item.package} {item.written_for}, but {item.source} has {item.installed}")
    ui.info("  Update each one for the new version (stale guidance is worse than none), then change its "
            "ralph-versions: entry, or the folder name for a vendored doc.")
    return 1


def run_stats(args) -> int:
    project, _ = load_project()
    effects = compare(read_records(project.iterations_log))
    if not effects:
        ui.info("  No iteration has used a skill yet (log/iterations.jsonl records which ones each used).")
        return 0
    ui.header("skills stats", "unit iterations that used each skill, against the ones that didn't")
    ui.info(f"  {'skill':<28} {'':>9} {'iters':>5} {'turns':>6} {'$':>6} {'done':>5}")
    for effect in effects:
        for label, group in (("used", effect.used), ("not used", effect.unused)):
            name = effect.skill if label == "used" else ""
            ui.info(f"  {name:<28} {label:>9} {group.iterations:>5} {group.turns:>6.1f} {group.cost_usd:>6.2f} "
                    f"{group.done:>5.0%}")
    ui.info("\n  A correlation, not an experiment: units that need a skill may be harder. Trust a difference "
            "only after a few dozen iterations.")
    return 0


def run_init(args) -> int:
    project, _ = load_project()
    compat.find_claude()
    versions = installed_versions(project.root)
    if not args.stack and not versions:
        raise RalphError("I can't tell what the stack is: no manifests or lockfiles were found.",
                         'Say it with --stack, e.g. --stack "FastAPI + React 19".')
    run_dir = project.log_dir / f"skills-{datetime.now():%Y%m%d-%H%M%S}"
    ui.info(f"  Drafting up to {args.max} skill(s) from {len(versions)} dependencies… (one call with no tools)")
    written = generate_stack_skills(CliRunner(), project.root, args.stack, versions, run_dir, max_skills=args.max)
    if not written:
        ui.info("  Nothing new: the skills Claude proposed already exist.")
        return 0
    for path in written:
        ui.ok(f"Drafted {path.relative_to(project.root)}")
    ui.info("  Read each one before relying on it (like the spec), fix any \"Decide:\" lines, then commit them.")
    return 0


def run_eval(args) -> int:
    project, _ = load_project()
    skills = [s for s in discover(project.root) if not args.name or s.name == args.name]
    if not skills:
        raise RalphError(f"There's no skill '{args.name}'." if args.name else "There are no skills to evaluate.",
                         "List them with 'ralph skills list'.")
    with_scenarios = [s for s in skills if scenarios(s)]
    for skill in skills:
        if skill not in with_scenarios:
            ui.warn(f"{skill.name} has no scenarios; add .claude/skills/{skill.name}/evals/<name>.json")
    if not with_scenarios:
        return 0
    compat.find_claude()
    run_dir = project.log_dir / f"evals-{datetime.now():%Y%m%d-%H%M%S}"
    failed = cost = 0
    for skill in with_scenarios:
        for result in run_evals(CliRunner(), skill, project.root, run_dir, model=args.model):
            cost += result.cost_usd
            problems = []
            if result.triggered != result.should_trigger:
                problems.append("used the skill when it shouldn't" if result.triggered else "didn't use the skill")
            if result.missing:
                problems.append(f"answer lacks {', '.join(result.missing)}")
            if problems:
                failed += 1
                ui.warn(f"{skill.name} / {result.scenario}: {'; '.join(problems)}")
            else:
                ui.ok(f"{skill.name} / {result.scenario}")
    ui.info(f"  ${cost:.3f} · logs in {run_dir}")
    return 1 if failed else 0
