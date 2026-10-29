"""Prompts and project templates live as files, never as strings in code.

`render()` fills `{{PLACEHOLDER}}` markers. A marker left unfilled is an
error, so a missing value can't reach Claude unnoticed.
"""

import json
import re
from pathlib import Path

from ralph.errors import RalphError

PACKAGE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"
TEMPLATES_DIR = PACKAGE_DIR / "templates"
SCHEMAS_DIR = PACKAGE_DIR / "schemas"

_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def fill(text: str, values: dict[str, str], source: str) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise RalphError(f"{source} needs a value for {{{{{key}}}}}, but none was given.")
        return values[key]

    return _PLACEHOLDER.sub(replace, text)


def render(name: str, **values: str) -> str:
    """A prompt from `ralph/prompts/`, with its placeholders filled."""
    path = PROMPTS_DIR / name
    return fill(path.read_text(encoding="utf-8"), values, f"prompts/{name}")


def render_template(name: str, **values: str) -> str:
    """A project template from `ralph/templates/`, with its placeholders filled."""
    path = TEMPLATES_DIR / name
    return fill(path.read_text(encoding="utf-8"), values, f"templates/{name}")


def schema(name: str) -> dict:
    """A JSON schema from `ralph/schemas/`."""
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
