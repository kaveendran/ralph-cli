"""The change ledger: what each finished unit changed, and who made it.

One JSON file per unit, `.ralph/ledger/<time>-<cr>-<unit>.json`, written
into the unit's own commit. Unique names make it append-only, so two
developers never conflict on it, and it reaches teammates only when you
publish with `ralph sync push`. The commit an entry belongs to isn't stored
in it (it doesn't exist yet when the entry is written); git knows it as the
commit that added the file.

Files and migrations come from git, so they're exact. Interfaces come from
the agent's report, cross-checked by `team.interfaces`, so they're a strong
hint rather than a guarantee.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic
from ralph.vcs.git import Git

LEDGER_DIR = ".ralph/ledger"
MIGRATION = re.compile(r"(^|/)(migrations|alembic/versions)/|\.sql$")
SUMMARY_LINES = 3


@dataclass
class LedgerEntry:
    id: str
    unit: str
    cr: str
    title: str = ""
    dev: str = ""
    email: str = ""
    at: str = ""
    base: str = ""                    # HEAD before the unit's commit
    files: list[dict] = field(default_factory=list)          # {"path", "status"}: A, M or D
    interfaces: list[dict] = field(default_factory=list)     # {"name", "change", "breaking", "source"}
    migrations: list[str] = field(default_factory=list)
    summary: str = ""
    acknowledged_impacts: list[str] = field(default_factory=list)
    # Filled in when reading, never stored:
    commit: str = ""
    pending: bool = False             # on the shared branch, not yet in yours

    @property
    def paths(self) -> list[str]:
        return [f["path"] for f in self.files]

    @property
    def breaking(self) -> list[dict]:
        return [i for i in self.interfaces if i.get("breaking")]

    @property
    def when(self) -> datetime | None:
        try:
            return datetime.strptime(self.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def to_json(self) -> dict:
        data = asdict(self)
        del data["commit"], data["pending"]
        return data

    @classmethod
    def from_json(cls, data: dict, commit: str = "", pending: bool = False) -> "LedgerEntry":
        known = set(cls.__dataclass_fields__) - {"commit", "pending"}
        return cls(**{k: v for k, v in data.items() if k in known}, commit=commit, pending=pending)


def new_entry(unit_id: str, cr: str, title: str, dev: str, email: str, base: str,
              files: list[tuple[str, str]], interfaces: list[dict], summary: str,
              acknowledged: list[str], now: datetime | None = None) -> LedgerEntry:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return LedgerEntry(
        id=f"{stamp}-{cr}-{unit_id}", unit=unit_id, cr=cr, title=title, dev=dev, email=email,
        at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), base=base,
        files=[{"path": path, "status": status} for status, path in files],
        interfaces=interfaces, migrations=[path for _, path in files if MIGRATION.search(path)],
        summary="\n".join(summary.strip().splitlines()[:SUMMARY_LINES]), acknowledged_impacts=list(acknowledged))


def write_entry(root: Path, entry: LedgerEntry) -> Path:
    path = root / LEDGER_DIR / f"{entry.id}.json"
    write_text_atomic(path, json.dumps(entry.to_json(), indent=2, ensure_ascii=False) + "\n")
    return path


def _parse(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("id") and data.get("unit") else None


def _added(git: Git, rev_range: list[str]) -> dict[str, str]:
    """ledger path → the commit that added it, for commits in the range."""
    output = git.run("log", "--diff-filter=A", "--name-only", "--format=commit %H", *rev_range, "--",
                     LEDGER_DIR, check=False).stdout
    added, commit = {}, ""
    for line in output.splitlines():
        if line.startswith("commit "):
            commit = line[7:].strip()
        elif line.strip().endswith(".json"):
            added.setdefault(line.strip(), commit)
    return added


def read_local(git: Git) -> list[LedgerEntry]:
    """Entries in your branch (the working tree), oldest first."""
    commits = _added(git, ["HEAD"]) if git.head() else {}
    entries = []
    for path in sorted((git.root / LEDGER_DIR).glob("*.json")):
        data = _parse(read_text(path))
        if data:
            entries.append(LedgerEntry.from_json(data, commits.get(f"{LEDGER_DIR}/{path.name}", "")))
    return entries


def read_pending(git: Git, ref: str) -> list[LedgerEntry]:
    """Entries on the local copy of the shared branch that your branch doesn't have yet."""
    if not ref or not git.head():
        return []
    entries = []
    for path, commit in sorted(_added(git, [f"HEAD..{ref}"]).items()):
        data = _parse(git.run("show", f"{ref}:{path}", check=False).stdout)
        if data:
            entries.append(LedgerEntry.from_json(data, commit, pending=True))
    return entries


def from_others(entries: list[LedgerEntry], my_email: str, days: int,
                now: datetime | None = None) -> list[LedgerEntry]:
    """Teammates' entries that can still matter: everything not pulled yet,
    and what landed in your branch in the last `days` days."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    return [e for e in entries
            if e.email.lower() != my_email.lower() and (e.pending or (e.when is not None and e.when >= since))]
