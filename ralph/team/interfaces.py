"""Which public functions and classes a change removed or reshaped.

This cross-checks what the agent reports in `interfaces_changed`. It reads
the files before (at HEAD) and after (in the working tree), with Python's
`ast` for Python and the `export` names for TypeScript and JavaScript.
It is best-effort: a removed name or a new required parameter is reliable;
a change in behaviour isn't visible here at all.
"""

import ast
import re

from ralph.vcs.git import Git

TS_EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
                       r"(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M)
MAX_PER_FILE = 20


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[tuple[str, ...], int]:
    """(parameter names, how many are required)."""
    args = node.args
    positional = [a.arg for a in args.posonlyargs + args.args if a.arg not in ("self", "cls")]
    keyword = [a.arg for a in args.kwonlyargs]
    required = len(positional) - len(args.defaults) + sum(d is None for d in args.kw_defaults)
    return tuple(positional + keyword), max(required, 0)


def python_api(source: str) -> dict[str, tuple]:
    """Public top-level functions and classes, and the public methods of public classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    api: dict[str, tuple] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            api[f"{node.name}()"] = _signature(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            api[node.name] = ()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        (not item.name.startswith("_") or item.name == "__init__"):
                    api[f"{node.name}.{item.name}()"] = _signature(item)
    return api


def ts_api(source: str) -> dict[str, tuple]:
    return {name: () for name in TS_EXPORT.findall(source)}


def _reader(path: str):
    if path.endswith(".py"):
        return python_api
    if path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
        return ts_api
    return None


def _breaks(old: tuple, new: tuple) -> bool:
    if not old or not new:          # classes and TS exports: no signature to compare
        return False
    old_names, old_required = old
    new_names, new_required = new
    return bool(set(old_names) - set(new_names)) or new_required > old_required


def compare(path: str, before: str, after: str | None) -> list[dict]:
    """Removed and reshaped public names between two versions of a file."""
    read = _reader(path)
    if read is None or not before:
        return []                   # a new file can't break anyone
    old, new = read(before), read(after or "")
    found = []
    for name, signature in old.items():
        if name not in new:
            found.append({"name": name, "change": f"removed from {path}", "breaking": True})
        elif signature != new[name]:
            breaking = _breaks(signature, new[name])
            found.append({"name": name, "change": f"signature changed in {path}", "breaking": breaking})
    return found[:MAX_PER_FILE]


def detect(git: Git, paths: list[str]) -> list[dict]:
    """Compare each changed source file at HEAD with the working tree."""
    found = []
    for path in paths:
        if _reader(path) is None:
            continue
        before = git.run("show", f"HEAD:{path}", check=False).stdout
        target = git.root / path
        after = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        found += compare(path, before, after)
    return found


def _key(name: str) -> str:
    return name.strip().rstrip("()").lower()


def merge(reported: list[dict], detected: list[dict]) -> list[dict]:
    """The agent's report, plus what detection found that it didn't mention.
    A change either side calls breaking counts as breaking."""
    merged = {_key(i["name"]): {**i, "breaking": bool(i.get("breaking")), "source": "agent"}
              for i in reported if i.get("name")}
    for item in detected:
        key = _key(item["name"])
        if key in merged:
            merged[key]["breaking"] = merged[key]["breaking"] or item["breaking"]
            merged[key]["source"] = "agent+detected"
        else:
            merged[key] = {**item, "source": "detected"}
    return list(merged.values())
