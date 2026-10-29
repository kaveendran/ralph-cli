"""One loop per project at a time.

The lock file holds the owning process ID. A lock left behind by a process
that no longer exists (a crash, a closed terminal) is taken over.
"""

import json
import os
from pathlib import Path

from ralph.errors import RalphError
from ralph.monitor.telemetry import now_iso


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self._owner()
                if owner and _alive(owner.get("pid", -1)):
                    raise RalphError(
                        f"Another ralph loop is already running here (pid {owner['pid']}, since {owner.get('since')}).",
                        f"Stop it first, or delete {self.path} if that process is gone.") from None
                self.path.unlink(missing_ok=True)   # stale: its process is gone
                continue
            with os.fdopen(fd, "w") as handle:
                json.dump({"pid": os.getpid(), "since": now_iso()}, handle)
            return self
        raise RalphError(f"Couldn't take the run lock at {self.path}.")

    def __exit__(self, *exc) -> None:
        owner = self._owner()
        if owner and owner.get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)

    def _owner(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
