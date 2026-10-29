"""How teammates' changes affect a unit, from what's already on your machine.

  DUPLICATE a teammate already finished this very unit; the loop stops rather
            than build it twice
  CONFLICT  your branch and the local copy of the shared branch can't merge
            cleanly (`git merge-tree`); the loop stops when it's in the unit's files
  BREAKING  a teammate changed an interface in a way that breaks callers, and the
            unit's plan entry or files use it
  OVERLAP   a teammate changed files the unit plans to change or already changed
  CLAIM-OVERLAP  a teammate has claimed another unit that plans to change the same files
  INFO      everything else (shown by `ralph changes`, never in the pack)

BREAKING and OVERLAP lines go into the unit's pack, at most 3,000 characters.
"""

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ralph.config import TeamConfig
from ralph.fsutil import read_text
from ralph.model.unit import Unit
from ralph.team.claims import Claim, others_claims, read_claims
from ralph.team.ledger import LedgerEntry, from_others, read_local, read_pending
from ralph.team.shared import Shared, last_fetch, local_ref
from ralph.vcs.git import Git

DIGEST_CHARS = 3_000
HTTP_ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)", re.I)


@dataclass
class Impact:
    kind: str                 # CONFLICT, BREAKING, OVERLAP or INFO
    entry: LedgerEntry | None
    detail: str
    files: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        if self.entry is None:
            return f"[{self.kind}] {self.detail}"
        e = self.entry
        where = f"{e.unit} · {e.dev or e.email} · {e.commit[:7] or 'uncommitted'}"
        pulled = " · not pulled yet" if e.pending else ""
        return f"[{self.kind}] {self.detail} ({where}{pulled}) — ack: {e.id}"


@dataclass
class TeamView:
    """What this clone knows about teammates' work, as of its last fetch."""
    shared: Shared | None
    ref: str                                  # the local copy of the shared branch, "" if never fetched
    entries: list[LedgerEntry]                # teammates' entries that can still matter
    conflicts: list[str]                      # files that wouldn't merge cleanly
    last_fetch: datetime | None
    claims: list[Claim] = field(default_factory=list)      # teammates' live claims


def team_view(git: Git, config: TeamConfig, my_email: str, now: datetime | None = None) -> TeamView:
    shared, ref = local_ref(git, config)
    entries = from_others(read_local(git) + read_pending(git, ref), my_email, config.impact_days, now)
    claims = list(others_claims(read_claims(git), my_email, now=now).values())
    return TeamView(shared, ref, entries, merge_conflicts(git, ref), last_fetch(git), claims)


def claim_overlaps(unit: Unit, claims: list[Claim]) -> list[Impact]:
    mine = sorted(set(unit.key_files) | set(unit.wip_paths))
    impacts = []
    for claim in claims:
        if (claim.cr, claim.unit) == (unit.cr, unit.id):
            continue
        shared = [p for p in claim.planned_files if touches(p, mine) or any(touches(m, [p]) for m in mine)]
        if shared:
            impacts.append(Impact("CLAIM-OVERLAP", None, f"{claim.cr}/{claim.unit} is claimed by "
                                  f"{claim.dev or claim.email} until {claim.expires_at[:10]} and plans to change "
                                  f"{', '.join(shared[:4])}: coordinate before you change them", shared))
    return impacts


def assess(view: TeamView, unit: Unit, unit_text: str, root: Path) -> "ImpactReport":
    mine = unit.key_files + unit.wip_paths
    return ImpactReport(classify(unit, unit_text, view.entries, root) + claim_overlaps(unit, view.claims),
                        conflicts_here=[p for p in view.conflicts if touches(p, mine)],
                        last_fetch=view.last_fetch)


@dataclass
class ImpactReport:
    impacts: list[Impact] = field(default_factory=list)
    conflicts_here: list[str] = field(default_factory=list)   # conflicted files among the unit's
    last_fetch: datetime | None = None

    @property
    def relevant(self) -> list[Impact]:
        return [i for i in self.impacts if i.kind in ("BREAKING", "OVERLAP", "CLAIM-OVERLAP")]

    @property
    def duplicates(self) -> list[Impact]:
        return [i for i in self.impacts if i.kind == "DUPLICATE"]

    def digest(self, now: datetime | None = None) -> str:
        """The pack section's text, or "" when nothing affects the unit."""
        lines = [i.line for i in self.relevant]
        if not lines:
            return ""
        text = f"As of your last fetch{' (' + age(self.last_fetch, now) + ')' if self.last_fetch else ''}. " \
               "Check these before you rely on how the code used to work; put the `ack:` IDs you handled " \
               "in `acknowledged_impacts`.\n\n"
        for shown, line in enumerate(lines):
            if len(text) + len(line) + 3 > DIGEST_CHARS:
                text += f"- …and {len(lines) - shown} more (see `ralph impact`)\n"
                break
            text += f"- {line}\n"
        return text.rstrip("\n")


def age(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return "never"
    hours = ((now or datetime.now(timezone.utc)) - moment).total_seconds() / 3600
    return f"{hours * 60:.0f} min ago" if hours < 1 else f"{hours:.0f} h ago" if hours < 48 else f"{hours / 24:.0f} days ago"


def touches(path: str, patterns: list[str]) -> bool:
    """A file matches a unit's key files: the same path, a glob, or a folder."""
    for pattern in patterns:
        if path == pattern or fnmatch.fnmatch(path, pattern) or \
                (pattern.endswith("/") and path.startswith(pattern)):
            return True
    return False


def symbol(name: str) -> str:
    """What to search callers for: `lookup()` → lookup, `Searcher.find()` → find,
    `GET /search` → /search."""
    route = HTTP_ROUTE.match(name.strip())
    if route:
        return route.group(2)
    bare = re.sub(r"\(.*$", "", name.strip())
    return re.split(r"[.:\s]", bare)[-1] if bare else ""


def _uses(name: str, texts: dict[str, str]) -> list[str]:
    target = symbol(name)
    if len(target) < 3:
        return []
    pattern = re.compile(re.escape(target) if target.startswith("/") else rf"\b{re.escape(target)}\b")
    return [where for where, text in texts.items() if pattern.search(text)]


def classify(unit: Unit, unit_text: str, entries: list[LedgerEntry], root: Path) -> list[Impact]:
    mine = sorted(set(unit.key_files) | set(unit.wip_paths))
    texts = {"your plan entry": unit_text}
    for path in mine:
        if (root / path).is_file():
            texts[path] = read_text(root / path)
    impacts = []
    for entry in entries:
        if entry.unit == unit.id and entry.cr == unit.cr:
            impacts.append(Impact("DUPLICATE", entry, f"{entry.dev or entry.email} already finished {unit.id}",
                                  entry.paths))
            continue
        if entry.id in unit.acknowledged_impacts:
            continue
        found = False
        for interface in entry.breaking:
            used_in = [w for w in _uses(interface["name"], texts) if w not in entry.paths]
            if used_in:
                impacts.append(Impact("BREAKING", entry, f"{interface['name']}: {interface.get('change') or 'changed'}"
                                      f" — used in {', '.join(used_in[:3])}", used_in))
                found = True
        overlap = [p for p in entry.paths if touches(p, mine)]
        if overlap:
            impacts.append(Impact("OVERLAP", entry, f"{', '.join(overlap[:4])}"
                                  f"{' and more' if len(overlap) > 4 else ''} changed by {entry.unit}", overlap))
            found = True
        if not found:
            impacts.append(Impact("INFO", entry, f"{entry.unit} {entry.title}".strip(), entry.paths))
    return impacts


def merge_conflicts(git: Git, ref: str) -> list[str]:
    """Files that would conflict if you merged the local copy of the shared branch. Local only."""
    if not ref or not git.head():
        return []
    if git.run("merge-base", "--is-ancestor", ref, "HEAD", check=False).returncode == 0:
        return []                 # nothing new on the shared branch
    result = git.run("merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", ref, check=False)
    if result.returncode != 1:
        return []                 # 0: merges cleanly; anything else: not comparable (e.g. unrelated histories)
    return [line for line in result.stdout.splitlines()[1:] if line.strip()]
