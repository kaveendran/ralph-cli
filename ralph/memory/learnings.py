"""Lessons the agent reported, in `.ralph/learnings/inbox.jsonl`.

Each line is one lesson: `{unit, cr, at, trigger, rule, evidence, paths}`,
plus `status` once you've reviewed it (`promoted` or `dismissed`). The
same lesson reported by different units is grouped, because a lesson that
recurs is the one worth keeping.
"""

import fnmatch
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic

_WORD = re.compile(r"[a-z0-9_./-]+")
STOPWORDS = frozenset("a an and are as at be by for from in is it of on or the this to use when with".split())


@dataclass
class Learning:
    rule: str
    trigger: str = ""
    evidence: str = ""
    paths: list[str] = field(default_factory=list)
    unit: str = ""
    cr: str = ""
    at: str = ""
    status: str = "new"           # new | promoted | dismissed
    promoted_to: str = ""

    @property
    def key(self) -> str:
        return hashlib.sha1(normalise(self.rule).encode()).hexdigest()[:10]


@dataclass
class LearningGroup:
    items: list[Learning]

    @property
    def rule(self) -> str:
        return self.items[0].rule

    @property
    def units(self) -> list[str]:
        return sorted({i.unit for i in self.items if i.unit})

    @property
    def paths(self) -> list[str]:
        return sorted({p for i in self.items for p in i.paths})

    @property
    def status(self) -> str:
        return self.items[0].status


def normalise(rule: str) -> str:
    return " ".join(_WORD.findall(rule.lower()))


def _words(rule: str) -> set[str]:
    return {w for w in _WORD.findall(rule.lower()) if w not in STOPWORDS}


def similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Two lessons say the same thing (enough word overlap)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def read_inbox(path: Path) -> list[Learning]:
    known = set(Learning.__dataclass_fields__)
    items = []
    for line in read_text(path).splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("rule"):
            items.append(Learning(**{k: v for k, v in data.items() if k in known}))
    return items


def write_inbox(path: Path, items: list[Learning]) -> None:
    write_text_atomic(path, "".join(json.dumps(asdict(i), ensure_ascii=False) + "\n" for i in items))


def group(items: list[Learning]) -> list[LearningGroup]:
    """Group lessons that say the same thing. Most-recurring groups first."""
    groups: list[LearningGroup] = []
    for item in items:
        match = next((g for g in groups if similar(g.rule, item.rule)), None)
        if match:
            match.items.append(item)
        else:
            groups.append(LearningGroup([item]))
    return sorted(groups, key=lambda g: (-len(g.units), g.items[0].at))


def _applies(paths: list[str], files: list[str]) -> bool:
    for pattern in paths:
        prefix = pattern.split("*")[0]
        if any(fnmatch.fnmatch(f, pattern) or (prefix and f.startswith(prefix)) for f in files):
            return True
    return False


def relevant(items: list[Learning], key_files: list[str], limit: int = 5) -> list[Learning]:
    """Lessons for a unit's context pack: unreviewed ones whose paths match the
    unit's files first, then general ones (without paths). Promoted lessons
    are left out: they now live in rules or CLAUDE.md, which Claude loads itself."""
    fresh = [i for i in items if i.status == "new"]
    matching = [i for i in fresh if i.paths and _applies(i.paths, key_files)]
    general = [i for i in fresh if not i.paths]
    picked, seen = [], set()
    for item in reversed(matching + general):        # newest first within each kind
        if item.key not in seen:
            seen.add(item.key)
            picked.append(item)
    ordered = [i for i in picked if i in matching] + [i for i in picked if i not in matching]
    return ordered[:limit]
