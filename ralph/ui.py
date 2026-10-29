"""Terminal output and prompts.

Everything the harness prints goes through here, so formatting stays
consistent. Regular output goes to stdout; warnings and errors go to stderr.
Colour is used only when the stream is a terminal and NO_COLOR isn't set.
"""

import os
import sys


def _colour(stream, code: str, text: str) -> str:
    if stream.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return text


def info(message: str = "") -> None:
    print(message)


def ok(message: str) -> None:
    print(f"  {_colour(sys.stdout, '32', '✓')}  {message}")


def warn(message: str) -> None:
    print(f"  {_colour(sys.stderr, '33', '!')}  {message}", file=sys.stderr)


def error(message: str, hint: str = "") -> None:
    print(f"  {_colour(sys.stderr, '31', '✗')}  {message}", file=sys.stderr)
    if hint:
        print(f"     {hint}", file=sys.stderr)


def header(title: str, subtitle: str = "") -> None:
    rule = "━" * 60
    print()
    print(rule)
    print(f"  ralph — {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(rule)


def section(title: str) -> None:
    print()
    print(f"  ── {title}")


def progress_bar(done: int, total: int, width: int = 24) -> str:
    """A text bar like `████████░░░░  8/12 units (66%)`."""
    if total <= 0:
        return "no units yet"
    filled = done * width // total
    return f"{'█' * filled}{'░' * (width - filled)}  {done}/{total} units ({done * 100 // total}%)"


def ask(prompt: str, default: str = "") -> str:
    """Read a line from the user. Returns `default` for an empty answer or no terminal."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.strip() or default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def confirm(prompt: str, default: bool = True) -> bool:
    choice = "Y/n" if default else "y/N"
    answer = ask(f"{prompt} [{choice}]").lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def choose(prompt: str, options: list[str], default: int = 0) -> str:
    """Ask the user to pick one option by number."""
    print(f"{prompt}:")
    for number, option in enumerate(options, start=1):
        print(f"  {number}) {option}")
    answer = ask("Choice", str(default + 1))
    try:
        index = int(answer) - 1
    except ValueError:
        index = default
    return options[index] if 0 <= index < len(options) else options[default]
