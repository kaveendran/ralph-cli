"""A unit of work and its state.

Plan fields (title, dependencies, spec refs, …) come from the plan and are
refreshed whenever the plan is compiled. State fields (status, attempts,
handoff, …) belong to the harness and survive recompiles.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


PLAN_FIELDS = ("title", "type", "priority", "size", "spend", "milestone", "order",
               "depends_on", "external_deps", "spec_refs", "key_files")


@dataclass
class Unit:
    id: str
    title: str
    cr: str
    # From the plan.
    type: str = ""
    priority: str = ""           # "P0".."P3", or "" when the plan doesn't say
    size: str = ""               # "S", "M", "L", or ""
    spend: str = ""              # "paid" when the unit spends money, else "" or "none"
    milestone: str = ""
    order: int = 0               # position in the plan's build order; 0 means unknown
    depends_on: list[str] = field(default_factory=list)       # units in the same CR
    external_deps: list[str] = field(default_factory=list)    # e.g. "cr1:U017", informational
    spec_refs: list[str] = field(default_factory=list)        # e.g. "§4.3", "cr1:§4.4", "AC-012"
    key_files: list[str] = field(default_factory=list)
    description: str = ""        # for units that have no plan entry (discovered or migrated)
    # State, owned by the harness.
    status: Status = Status.TODO
    attempts: int = 0
    blocker: str = ""
    handoff: str = ""
    done: dict = field(default_factory=dict)                  # {"commit", "by", "at"} once done
    discovered_from: str = ""
    triage: bool = False         # found during another unit; waits for a human to accept it
    allow_large: bool = False    # the size check is waived for this unit
    last_review_gaps: list[str] = field(default_factory=list)
    # Work in progress across attempts: the paths this unit's runs changed so
    # far, and those among them you had already changed yourself.
    wip_paths: list[str] = field(default_factory=list)
    wip_overlap: list[str] = field(default_factory=list)
    # Teammates' ledger entries the agent has dealt with, so they leave the pack.
    acknowledged_impacts: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Unit":
        data = dict(data)
        data["status"] = Status(data.get("status", Status.TODO.value))
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_large(self) -> bool:
        return self.size == "L"
