"""`ralph steer`, `ralph pause`, `ralph resume` — talk to a running loop."""

from ralph import ui
from ralph.commands.common import load_project
from ralph.kernel.control import Control


def register(subparsers) -> None:
    s = subparsers.add_parser("steer", help="add a note to the next unit pack, without stopping the loop")
    s.add_argument("note", help="what the next iteration should know or do differently")
    s.add_argument("--hard", metavar="COMMAND", help="also add a check that must pass before any unit counts as done")
    s.set_defaults(handler=run_steer)

    subparsers.add_parser("pause", help="let the current iteration finish, then wait").set_defaults(handler=run_pause)
    subparsers.add_parser("resume", help="continue a paused loop").set_defaults(handler=run_resume)


def _control() -> Control:
    project, _ = load_project()
    return Control(project.control_dir)


def run_steer(args) -> int:
    control = _control()
    control.add_steer(args.note)
    if args.hard:
        control.add_check(args.hard)
        ui.ok(f"Added a check every unit must pass: {args.hard}")
    ui.ok("The next unit pack will include your note.")
    return 0


def run_pause(args) -> int:
    _control().pause()
    ui.ok("The loop will pause after the current iteration. Run 'ralph resume' to continue.")
    return 0


def run_resume(args) -> int:
    _control().resume()
    ui.ok("Resumed.")
    return 0
