"""Read and write the YAML frontmatter of skills and rules, without a YAML library.

Only the subset these files use is supported:

    ---
    name: fastapi-conventions
    description: How this project writes FastAPI endpoints.
    paths: [backend/**, "api/*.py"]       # or a block list:
    tags:
      - api
    ralph-versions:                       # one level of nesting
      fastapi: "0.115"
    ---
"""

import re

_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _inline_list(text: str) -> list[str]:
    inner = text.strip()[1:-1]
    return [_scalar(part) for part in inner.split(",") if part.strip()]


def parse(lines: list[str]) -> dict:
    data: dict = {}
    key = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw.startswith((" ", "\t"))
        line = raw.strip()
        if indented and key is not None:
            if line.startswith("- "):
                if not isinstance(data[key], list):
                    data[key] = []
                data[key].append(_scalar(line[2:]))
            else:
                match = _KEY.match(line)
                if match:
                    if not isinstance(data[key], dict):
                        data[key] = {}
                    data[key][match.group(1)] = _scalar(match.group(2))
            continue
        match = _KEY.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = _inline_list(value)
        elif value:
            data[key] = _scalar(value)
        else:
            data[key] = ""      # a block list or map follows
    return data


def split(text: str) -> tuple[dict, str]:
    """(frontmatter, body). A file without frontmatter gives ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return parse([line.rstrip("\r\n") for line in lines[1:index]]), "".join(lines[index + 1:]).lstrip("\n")
    return {}, text


def _quote(value: str) -> str:
    """Quote what YAML would misread: special characters, padding, and numbers
    (a version pin "2.10" must not become the float 2.1)."""
    special = re.search(r"[:#\[\]{},*&!|>'\"%@`]", value) or value != value.strip()
    return f'"{value}"' if special or re.fullmatch(r"[0-9][0-9.]*", value) else value


def render(meta: dict) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {_quote(str(item))}" for item in value]
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            lines += [f"  {k}: {_quote(str(v))}" for k, v in value.items()]
        else:
            lines.append(f"{key}: {_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n"
