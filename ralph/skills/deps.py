"""Which versions of its dependencies the project actually uses.

Lock files win over manifests, because they record what's installed:
uv.lock, poetry.lock and package-lock.json first, then pinned
requirements*.txt, then the ranges in pyproject.toml and package.json.
The project root and its first-level folders (backend/, frontend/, …) are read.
"""

import json
import re
import tomllib
from pathlib import Path

from ralph.fsutil import read_text

PIN = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*==\s*([0-9][^\s;,#]*)")
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:[<>=~!]=?|@)\s*v?([0-9][0-9.]*)")
RANGE = re.compile(r"([0-9]+(?:\.[0-9]+)*)")


def package_name(text: str) -> str:
    return re.sub(r"[-_.]+", "-", text.split("[")[0].strip().lower())


def _from_lock_toml(path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError:
        return {}
    return {package_name(p["name"]): str(p["version"]) for p in data.get("package", []) if "name" in p and "version" in p}


def _from_package_lock(path: Path) -> dict[str, str]:
    try:
        data = json.loads(read_text(path) or "{}")
    except json.JSONDecodeError:
        return {}
    versions = {}
    for key, info in (data.get("packages") or {}).items():
        if key.startswith("node_modules/") and "/node_modules/" not in key[13:] and info.get("version"):
            versions[package_name(key[13:])] = info["version"]
    return versions


def _from_requirements(path: Path) -> dict[str, str]:
    return {package_name(m.group(1)): m.group(2) for m in map(PIN.match, read_text(path).splitlines()) if m}


def _from_pyproject(path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError:
        return {}
    versions = {}
    for spec in (data.get("project") or {}).get("dependencies", []):
        match = REQUIREMENT.match(spec)
        if match:
            versions[package_name(match.group(1))] = match.group(2)
    return versions


def _from_package_json(path: Path) -> dict[str, str]:
    try:
        data = json.loads(read_text(path) or "{}")
    except json.JSONDecodeError:
        return {}
    versions = {}
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            match = RANGE.search(str(spec))
            if match:
                versions[package_name(name)] = match.group(1)
    return versions


READERS = (  # lowest priority first; later readers overwrite earlier ones
    ("package.json", _from_package_json), ("pyproject.toml", _from_pyproject),
    ("requirements.txt", _from_requirements), ("requirements-dev.txt", _from_requirements),
    ("poetry.lock", _from_lock_toml), ("uv.lock", _from_lock_toml), ("package-lock.json", _from_package_lock),
)


def installed_versions(root: Path) -> dict[str, tuple[str, str]]:
    """package name → (version, the file it came from)."""
    folders = [root] + sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "node_modules")))
    found: dict[str, tuple[str, str]] = {}
    for filename, reader in READERS:
        for folder in folders:
            path = folder / filename
            if path.is_file():
                for name, version in reader(path).items():
                    found[name] = (version, str(path.relative_to(root)))
    return found
