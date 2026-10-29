"""Which unit runs next.

A unit is *ready* when all its dependencies (in the same CR) are done.
Order of preference:
1. a unit already in progress (finish what was started);
2. otherwise the next ready unit in the plan's build order;
3. ties broken by priority (P0 first), then by how many units wait on it
   (the critical path), so the work that unblocks the most goes first.
Blocked units only hold back the units that depend on them. Triage units
(found during other work) wait until a human accepts them.
"""

from ralph.errors import RalphError
from ralph.model.unit import Status, Unit

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def find_cycle(units: dict[str, Unit]) -> list[str] | None:
    """A dependency cycle such as ['U003', 'U005', 'U003'], or None."""
    visiting, done = set(), set()

    def visit(unit_id: str, path: list[str]) -> list[str] | None:
        if unit_id in visiting:
            return path[path.index(unit_id):] + [unit_id]
        if unit_id in done or unit_id not in units:
            return None
        visiting.add(unit_id)
        for dep in units[unit_id].depends_on:
            cycle = visit(dep, path + [unit_id])
            if cycle:
                return cycle
        visiting.discard(unit_id)
        done.add(unit_id)
        return None

    for unit_id in units:
        cycle = visit(unit_id, [])
        if cycle:
            return cycle
    return None


def waiting_counts(units: dict[str, Unit]) -> dict[str, int]:
    """For each unit, how many units transitively depend on it."""
    dependents: dict[str, set[str]] = {u: set() for u in units}
    for unit in units.values():
        for dep in unit.depends_on:
            if dep in dependents:
                dependents[dep].add(unit.id)

    memo: dict[str, set[str]] = {}

    def downstream(unit_id: str) -> set[str]:
        if unit_id not in memo:
            memo[unit_id] = set()
            for child in dependents[unit_id]:
                memo[unit_id] |= {child} | downstream(child)
        return memo[unit_id]

    return {unit_id: len(downstream(unit_id)) for unit_id in units}


def is_ready(unit: Unit, units: dict[str, Unit]) -> bool:
    if unit.status not in (Status.TODO, Status.IN_PROGRESS) or unit.triage:
        return False
    return all(units[dep].status is Status.DONE for dep in unit.depends_on if dep in units)


def ready_units(units: dict[str, Unit], only: set[str] | None = None) -> list[Unit]:
    """Ready units in the order they would run."""
    cycle = find_cycle(units)
    if cycle:
        raise RalphError(f"The plan has a dependency cycle: {' → '.join(cycle)}",
                         "Fix the Depends On lines in the plan, then recompile with 'ralph graph compile'.")
    waits = waiting_counts(units)
    candidates = [u for u in units.values() if is_ready(u, units) and (only is None or u.id in only)]
    return sorted(candidates, key=lambda u: (
        u.status is not Status.IN_PROGRESS,
        u.order or 10**6,
        PRIORITY_RANK.get(u.priority, 4),
        -waits.get(u.id, 0),
        u.id,
    ))


def next_unit(units: dict[str, Unit], only: set[str] | None = None) -> Unit | None:
    ready = ready_units(units, only)
    return ready[0] if ready else None


def why_nothing_is_ready(units: dict[str, Unit]) -> list[str]:
    """One line per unfinished unit, saying what it waits for."""
    reasons = []
    for unit in units.values():
        if unit.status is Status.DONE:
            continue
        if unit.status is Status.BLOCKED:
            reasons.append(f"{unit.id} is blocked: {unit.blocker or 'no reason recorded'}")
        elif unit.triage:
            reasons.append(f"{unit.id} needs triage (found during {unit.discovered_from}); "
                           f"accept it with 'ralph set {unit.id} --status todo'")
        else:
            waiting = [d for d in unit.depends_on if d in units and units[d].status is not Status.DONE]
            if waiting:
                states = ", ".join(f"{d} ({units[d].status.value})" for d in waiting)
                reasons.append(f"{unit.id} waits for {states}")
    return reasons
