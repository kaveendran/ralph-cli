"""Talking to a running loop: steering notes and pausing.

`ralph steer "…"` leaves a note that the next unit pack includes, so you can
redirect the loop without stopping it. `--hard "<command>"` also adds a check
that must pass before any unit counts as done. `ralph pause` makes the loop
wait after the current iteration until `ralph resume`.
"""

import json
import time
from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic


class Control:
    def __init__(self, control_dir: Path):
        self.dir = control_dir

    @property
    def steer_file(self) -> Path:
        return self.dir / "steer.md"

    @property
    def checks_file(self) -> Path:
        return self.dir / "checks.json"

    @property
    def pause_file(self) -> Path:
        return self.dir / "pause"

    def add_steer(self, note: str) -> None:
        existing = read_text(self.steer_file).rstrip()
        write_text_atomic(self.steer_file, f"{existing}\n- {note.strip()}\n".lstrip())

    def take_steer(self) -> str:
        """The pending notes; they are delivered once, then cleared."""
        notes = read_text(self.steer_file).strip()
        if notes:
            self.steer_file.unlink(missing_ok=True)
        return notes

    def add_check(self, command: str) -> None:
        write_text_atomic(self.checks_file, json.dumps(self.checks() + [command], indent=2) + "\n")

    def checks(self) -> list[str]:
        text = read_text(self.checks_file)
        return json.loads(text) if text.strip() else []

    def pause(self) -> None:
        write_text_atomic(self.pause_file, "paused\n")

    def resume(self) -> None:
        self.pause_file.unlink(missing_ok=True)

    @property
    def paused(self) -> bool:
        return self.pause_file.exists()

    def wait_while_paused(self, on_wait=None, poll_seconds: float = 5.0) -> None:
        announced = False
        while self.paused:
            if not announced and on_wait:
                on_wait()
                announced = True
            time.sleep(poll_seconds)
