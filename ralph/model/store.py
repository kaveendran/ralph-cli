"""Unit state on disk: one JSON file per unit, plus an optional notes file.

One file per unit means two developers working on different units never
touch the same file, so their branches merge without conflicts. Only the
harness writes these files.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic
from ralph.model.unit import Unit


class UnitStore:
    def __init__(self, units_dir: Path):
        self.dir = units_dir

    def exists(self) -> bool:
        return self.dir.is_dir() and any(self.dir.glob("*.json"))

    def path(self, unit_id: str) -> Path:
        return self.dir / f"{unit_id}.json"

    def notes_path(self, unit_id: str) -> Path:
        return self.dir / f"{unit_id}.notes.md"

    def load(self) -> dict[str, Unit]:
        """All units, in build order (then by ID)."""
        units = []
        for path in sorted(self.dir.glob("*.json")):
            units.append(Unit.from_json(json.loads(path.read_text(encoding="utf-8"))))
        units.sort(key=lambda u: (u.order or 10**6, u.id))
        return {u.id: u for u in units}

    def save(self, unit: Unit) -> None:
        text = json.dumps(unit.to_json(), indent=2, ensure_ascii=False) + "\n"
        write_text_atomic(self.path(unit.id), text)

    def save_all(self, units) -> None:
        for unit in units:
            self.save(unit)

    def append_note(self, unit_id: str, heading: str, body: str) -> None:
        """Add a dated note, e.g. a failure summary or a steering message."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        existing = read_text(self.notes_path(unit_id))
        entry = f"## {heading} ({stamp})\n\n{body.strip()}\n"
        write_text_atomic(self.notes_path(unit_id), f"{existing.rstrip()}\n\n{entry}".lstrip())

    def recent_notes(self, unit_id: str, max_lines: int = 40) -> str:
        """The last `max_lines` lines of a unit's notes (only these go into context)."""
        lines = read_text(self.notes_path(unit_id)).strip().splitlines()
        return "\n".join(lines[-max_lines:])
