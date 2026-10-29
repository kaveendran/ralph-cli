"""Memory decay: summarise the notes of units finished long ago.

A done unit's notes (failures, review gaps, the migrated diary) stop being
useful once later work has moved on. Their full text moves to
`archive/notes/`, and a one-line summary stays, so history stays cheap
without being lost. Reviewed lessons move from the inbox to
`archive.jsonl` next to it the same way.
"""

from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic
from ralph.memory.learnings import read_inbox, write_inbox
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit

MIN_LINES = 12      # shorter notes aren't worth summarising


def candidates(units: dict[str, Unit], store: UnitStore, keep_recent: int) -> list[Unit]:
    done = sorted((u for u in units.values() if u.status is Status.DONE), key=lambda u: u.done.get("at", ""))
    old = done[:-keep_recent] if keep_recent else done
    return [u for u in old if len(read_text(store.notes_path(u.id)).splitlines()) >= MIN_LINES]


def summary(unit: Unit, archived: Path) -> str:
    first = (unit.handoff.strip().splitlines() or [unit.title])[0]
    when = unit.done.get("at", "")[:10]
    return f"Done{' on ' + when if when else ''}: {first}\n\nFull notes: `{archived}`\n"


def decay(units: dict[str, Unit], store: UnitStore, archive_dir: Path, root: Path, keep_recent: int = 5,
          dry_run: bool = False) -> list[str]:
    decayed = []
    for unit in candidates(units, store, keep_recent):
        archived = archive_dir / "notes" / f"{unit.id}.notes.md"
        if not dry_run:
            write_text_atomic(archived, read_text(store.notes_path(unit.id)))
            write_text_atomic(store.notes_path(unit.id), summary(unit, archived.relative_to(root)))
        decayed.append(unit.id)
    return decayed


def archive_reviewed(inbox: Path, dry_run: bool = False) -> int:
    """Move promoted and dismissed lessons to `archive.jsonl`. Returns how many moved."""
    items = read_inbox(inbox)
    reviewed = [i for i in items if i.status != "new"]
    if reviewed and not dry_run:
        archive = inbox.with_name("archive.jsonl")
        write_inbox(archive, read_inbox(archive) + reviewed)
        write_inbox(inbox, [i for i in items if i.status == "new"])
    return len(reviewed)
