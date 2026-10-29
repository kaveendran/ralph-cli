"""Suggest the 1–3 skills that fit a unit, so the agent doesn't have to discover them.

A skill scores when its `paths` match the unit's key files (the strongest
signal), when it names the unit's type, and when its description shares
words with the unit's title and plan entry.
"""

import fnmatch
import re

from ralph.memory.learnings import STOPWORDS
from ralph.model.unit import Unit
from ralph.skills.registry import Skill

_WORD = re.compile(r"[a-z][a-z0-9+#-]{2,}")
GENERIC = STOPWORDS | {"how", "this", "that", "project", "code", "file", "files", "used", "using", "writes",
                       "write", "when", "what", "where", "which", "about", "into", "skill", "unit", "should"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in GENERIC}


def score(skill: Skill, unit: Unit, unit_text: str) -> tuple[int, str]:
    if skill.paths and any(fnmatch.fnmatch(f, p) for f in unit.key_files for p in skill.paths):
        return 10, "its paths match this unit's files"
    points, reasons = 0, []
    if unit.type and unit.type in _words(skill.name + " " + skill.description):
        points += 3
        reasons.append(f"it covers {unit.type} work")
    shared = _words(skill.name.replace("-", " ") + " " + skill.description) & _words(unit.title + " " + unit_text)
    if shared:
        points += min(len(shared), 4)
        reasons.append("it mentions " + ", ".join(sorted(shared)[:3]))
    return points, "; ".join(reasons)


def suggest(skills: list[Skill], unit: Unit, unit_text: str = "", limit: int = 3,
            min_score: int = 3) -> list[tuple[Skill, str]]:
    scored = [(skill, *score(skill, unit, unit_text)) for skill in skills]
    ranked = sorted((s for s in scored if s[1] >= min_score), key=lambda s: -s[1])
    return [(skill, reason) for skill, _, reason in ranked[:limit]]
