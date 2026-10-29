"""Does a skill help? Compare the iterations that used it with the ones that didn't.

From log/iterations.jsonl: for each skill, how many unit iterations used it,
and for those against the rest, the average turns, the average cost, and how
often the iteration finished its unit. It's a correlation, not an experiment:
units that need a skill may simply be harder. Read it with that in mind, and
give it a few dozen iterations before trusting a difference.
"""

from dataclasses import dataclass


@dataclass
class Group:
    iterations: int = 0
    turns: float = 0.0
    cost_usd: float = 0.0
    done: float = 0.0          # share of iterations that finished their unit

    @classmethod
    def of(cls, records: list[dict]) -> "Group":
        if not records:
            return cls()
        agents = [r.get("agent") or {} for r in records]
        return cls(len(records), sum(a.get("num_turns") or 0 for a in agents) / len(records),
                   sum((a.get("total_cost_usd") or 0) for a in agents) / len(records),
                   sum(r.get("outcome") == "done" for r in records) / len(records))


@dataclass
class SkillEffect:
    skill: str
    used: Group
    unused: Group


def compare(records: list[dict]) -> list[SkillEffect]:
    """One row per skill seen in the log; unit iterations only (tasks and repairs are left out)."""
    units = [r for r in records if r.get("unit") not in ("task", "", None)]
    skills = sorted({s for r in units for s in (r.get("agent") or {}).get("skills_invoked", [])})
    effects = []
    for skill in skills:
        used = [r for r in units if skill in (r.get("agent") or {}).get("skills_invoked", [])]
        unused = [r for r in units if skill not in (r.get("agent") or {}).get("skills_invoked", [])]
        effects.append(SkillEffect(skill, Group.of(used), Group.of(unused)))
    return effects
