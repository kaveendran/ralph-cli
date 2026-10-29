"""Small file helpers shared across the package."""

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Write a file so readers never see it half-written.

    The text goes to a temporary file in the same directory, which then
    replaces the target in one rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_text(path: Path, default: str = "") -> str:
    """The file's text, or `default` when it doesn't exist."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def tail_lines(text: str, count: int) -> str:
    """The last `count` lines of `text`."""
    lines = text.rstrip("\n").splitlines()
    return "\n".join(lines[-count:])
