"""Claims and locks (docs/meta-harness-plan.md §11.3), as this clone last saw them.

A claim reserves a unit so two developers don't build the same thing; a
lock reserves a path that only one person should change at a time (a
migration folder, a schema file, a lockfile). Each is a git ref on the
shared remote that points to a commit holding one small JSON file:

    <namespace>/claims/<cr>/<unit>   → claim.json {unit, cr, dev, email, planned_files, base, claimed_at, expires_at}
    <namespace>/locks/<path hash>    → lock.json  {path, dev, email, reason, locked_at}

Creating one is an atomic push that fails if the ref already exists, so two
developers can't both get it. `ralph sync` and the claim commands copy the
remote's refs to `refs/ralph/team/…` here; the loop reads only that copy.
Claims expire (a lease, 48 h by default) so a forgotten one doesn't block
anyone for long; locks last until they're released.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from ralph.vcs.git import Git

MIRROR = "refs/ralph/team"          # where this clone keeps its copy of the remote's claims and locks
TIME = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, TIME).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class Claim:
    unit: str
    cr: str
    dev: str = ""
    email: str = ""
    planned_files: list[str] = field(default_factory=list)
    base: str = ""
    claimed_at: str = ""
    expires_at: str = ""
    sha: str = ""                    # the ref's commit, when read

    def expired(self, now: datetime | None = None) -> bool:
        expires = _parse_time(self.expires_at)
        return expires is not None and expires <= (now or _now())

    def held_by(self, email: str) -> bool:
        return bool(email) and self.email.lower() == email.lower()

    def to_json(self) -> dict:
        data = asdict(self)
        del data["sha"]
        return data


@dataclass
class Lock:
    path: str
    dev: str = ""
    email: str = ""
    reason: str = ""
    locked_at: str = ""
    sha: str = ""

    def held_by(self, email: str) -> bool:
        return bool(email) and self.email.lower() == email.lower()

    def to_json(self) -> dict:
        data = asdict(self)
        del data["sha"]
        return data


def claim_ref(namespace: str, cr: str, unit: str) -> str:
    return f"{namespace}/claims/{cr}/{unit}"


def lock_ref(namespace: str, path: str) -> str:
    return f"{namespace}/locks/{hashlib.sha1(path.encode('utf-8')).hexdigest()[:16]}"


def mirror_of(ref: str, namespace: str) -> str:
    """The local copy of a remote ref: <namespace>/claims/… → refs/ralph/team/claims/…"""
    return MIRROR + ref[len(namespace):]


def new_claim(unit: str, cr: str, dev: str, email: str, planned_files: list[str], base: str,
              hours: int, now: datetime | None = None) -> Claim:
    now = now or _now()
    return Claim(unit, cr, dev, email, list(planned_files), base, now.strftime(TIME),
                 (now + timedelta(hours=hours)).strftime(TIME))


def new_lock(path: str, dev: str, email: str, reason: str, now: datetime | None = None) -> Lock:
    return Lock(path, dev, email, reason, (now or _now()).strftime(TIME))


def record_commit(git: Git, filename: str, data: dict, message: str) -> str:
    """A commit holding just `filename` with `data`, made without touching the working tree or index."""
    blob = git.run("hash-object", "-w", "--stdin", input_text=json.dumps(data, indent=2) + "\n").stdout.strip()
    tree = git.run("mktree", input_text=f"100644 blob {blob}\t{filename}\n").stdout.strip()
    return git.run("commit-tree", tree, "-m", message).stdout.strip()


def _read(git: Git, prefix: str, filename: str) -> list[tuple[str, dict]]:
    listing = git.run("for-each-ref", "--format=%(objectname) %(refname)", prefix, check=False).stdout
    found = []
    for line in listing.splitlines():
        sha, _, ref = line.partition(" ")
        text = git.run("show", f"{sha}:{filename}", check=False).stdout
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            found.append((sha, data))
    return found


def read_claims(git: Git) -> list[Claim]:
    known = set(Claim.__dataclass_fields__) - {"sha"}
    return [Claim(**{k: v for k, v in data.items() if k in known}, sha=sha)
            for sha, data in _read(git, f"{MIRROR}/claims/", "claim.json") if data.get("unit")]


def read_locks(git: Git) -> list[Lock]:
    known = set(Lock.__dataclass_fields__) - {"sha"}
    return [Lock(**{k: v for k, v in data.items() if k in known}, sha=sha)
            for sha, data in _read(git, f"{MIRROR}/locks/", "lock.json") if data.get("path")]


def others_claims(claims: list[Claim], email: str, cr: str = "", now: datetime | None = None) -> dict[str, Claim]:
    """unit ID → a teammate's live claim on it."""
    return {c.unit: c for c in claims
            if not c.held_by(email) and not c.expired(now) and (not cr or c.cr == cr)}


def my_claims(claims: list[Claim], email: str, cr: str = "", now: datetime | None = None) -> dict[str, Claim]:
    return {c.unit: c for c in claims if c.held_by(email) and not c.expired(now) and (not cr or c.cr == cr)}
