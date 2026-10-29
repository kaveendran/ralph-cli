"""Turn a parsed plan into unit records, keeping the state of existing units.

Recompiling after the plan changes refreshes the plan fields (title,
dependencies, …) and leaves the harness's state (status, attempts, handoff)
alone. Units that exist on disk but are no longer in the plan are kept and
reported, never deleted.
"""

import hashlib
from dataclasses import dataclass, field

from ralph.model.plan import Plan, PlanUnit
from ralph.model.unit import PLAN_FIELDS, Unit

MAX_KEY_FILES = 8          # more than this and the unit is rejected: split it
MANY_SPEC_REFS = 6         # more than this is flagged: the unit may be doing too much


@dataclass
class SizeIssue:
    unit_id: str
    severity: str           # "reject" or "flag"
    reason: str


@dataclass
class CompileResult:
    units: dict[str, Unit]
    added: list[str] = field(default_factory=list)
    not_in_plan: list[str] = field(default_factory=list)
    issues: list[SizeIssue] = field(default_factory=list)

    @property
    def rejected(self) -> list[SizeIssue]:
        return [i for i in self.issues if i.severity == "reject"]


def _from_plan(plan_unit: PlanUnit, cr: str, order: int, local_ids: set[str]) -> Unit:
    # A dependency that isn't a unit of this CR belongs to another CR. Plans
    # often prefix only the first one ("CR1 U017, U018, …"), so treat any
    # unknown ID as informational rather than letting it block forever.
    depends_on = [d for d in plan_unit.depends_on if d in local_ids]
    external = plan_unit.external_deps + [f"other:{d}" for d in plan_unit.depends_on if d not in local_ids]
    return Unit(
        id=plan_unit.id, title=plan_unit.title, cr=cr, type=plan_unit.type,
        priority=plan_unit.priority, size=plan_unit.size, spend=plan_unit.spend,
        milestone=plan_unit.milestone, order=order, depends_on=depends_on,
        external_deps=external, spec_refs=plan_unit.spec_refs, key_files=plan_unit.key_files,
    )


def size_issues(unit: Unit) -> list[SizeIssue]:
    """Units too big for one session. Small units are what keep context small."""
    if unit.allow_large:
        return []
    issues = []
    if len(unit.key_files) > MAX_KEY_FILES:
        issues.append(SizeIssue(unit.id, "reject",
                                f"{len(unit.key_files)} key files (limit {MAX_KEY_FILES}); split it, "
                                f"or allow it with 'ralph set {unit.id} --allow-large'"))
    if unit.is_large:
        issues.append(SizeIssue(unit.id, "flag", "sized Large; consider splitting it"))
    if len(unit.spec_refs) > MANY_SPEC_REFS:
        issues.append(SizeIssue(unit.id, "flag",
                                f"cites {len(unit.spec_refs)} spec sections; only the first few fit in its context"))
    return issues


def compile_plan(plan: Plan, existing: dict[str, Unit], cr: str) -> CompileResult:
    order_index = {unit_id: n for n, unit_id in enumerate(plan.order, start=1)}
    fallback = len(order_index)
    local_ids = {u.id for u in plan.units} | set(existing)
    result = CompileResult(units={})

    for position, plan_unit in enumerate(plan.units, start=1):
        order = order_index.get(plan_unit.id, fallback + position)
        fresh = _from_plan(plan_unit, cr, order, local_ids)
        unit = existing.get(plan_unit.id)
        if unit is None:
            unit = fresh
            result.added.append(unit.id)
        else:
            for name in PLAN_FIELDS:
                setattr(unit, name, getattr(fresh, name))
        result.units[unit.id] = unit
        result.issues.extend(size_issues(unit))

    for unit_id, unit in existing.items():
        if unit_id not in result.units:
            result.units[unit_id] = unit
            if not unit.discovered_from:
                result.not_in_plan.append(unit_id)
    return result


def discovered_unit_id(parent_id: str, title: str) -> str:
    """An ID for a unit found during another unit: `U037-a1b2`.

    The hash suffix (from the parent and the title) means two developers who
    add units at the same time never pick the same ID.
    """
    digest = hashlib.sha1(f"{parent_id}:{title}".encode("utf-8")).hexdigest()[:4]
    base = parent_id.split("-")[0]
    return f"{base}-{digest}"
