"""Breaking changes in an OpenAPI document (JSON), for the ledger's interface check.

An operation is named like `POST /invoices`, so the impact check can find
callers by the route. Breaking: an operation removed, a new required
parameter, a request body that became required, or a new required field in
the request body. Additions aren't breaking. YAML documents are left out
(Python has no YAML parser built in); export JSON to have them checked.
"""

import json
import re

METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
FILENAME = re.compile(r"(^|/)(openapi|swagger)(\.[\w-]+)?\.json$|\.openapi\.json$", re.I)


def is_openapi(path: str) -> bool:
    return bool(FILENAME.search(path))


def _load(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) and ("openapi" in data or "swagger" in data) else {}


def _resolve(doc: dict, node):
    """Follow a local `$ref` (e.g. #/components/schemas/Invoice) one or more levels."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 10:
        target = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node, seen = target, seen + 1
    return node if isinstance(node, dict) else {}


def _operations(doc: dict) -> dict[str, dict]:
    found = {}
    for route, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters", [])
        for method in METHODS:
            if isinstance(item.get(method), dict):
                found[f"{method.upper()} {route}"] = {"op": item[method], "shared": shared}
    return found


def _required_parameters(doc: dict, entry: dict) -> set[str]:
    params = [_resolve(doc, p) for p in entry["shared"] + entry["op"].get("parameters", [])]
    return {f"{p.get('in', '?')}:{p.get('name', '?')}" for p in params if p.get("required")}


def _body(doc: dict, entry: dict) -> tuple[bool, set[str]]:
    """(is the body required, the required fields of its JSON schema)."""
    body = _resolve(doc, entry["op"].get("requestBody") or {})
    content = body.get("content") or {}
    media = (content.get("application/json") or next(iter(content.values()), {})) if content else {}
    schema = _resolve(doc, (media or {}).get("schema") or {})
    return bool(body.get("required")), set(schema.get("required") or [])


def compare(path: str, before: str, after: str | None) -> list[dict]:
    old, new = _load(before), _load(after or "")
    if not old:
        return []
    old_ops, new_ops = _operations(old), _operations(new)
    found = []
    for name, entry in old_ops.items():
        if name not in new_ops:
            found.append({"name": name, "change": f"removed from {path}", "breaking": True})
            continue
        changes = []
        added = sorted(_required_parameters(new, new_ops[name]) - _required_parameters(old, entry))
        if added:
            changes.append(f"now requires {', '.join(added)}")
        was_required, old_fields = _body(old, entry)
        is_required, new_fields = _body(new, new_ops[name])
        if is_required and not was_required:
            changes.append("the request body became required")
        if new_fields - old_fields:
            changes.append(f"the request body now requires {', '.join(sorted(new_fields - old_fields))}")
        if changes:      # one entry per operation: the ledger merges entries by name
            found.append({"name": name, "change": "; ".join(changes), "breaking": True})
    return found
