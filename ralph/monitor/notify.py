"""Tell you when the loop needs you: a unit blocked, the loop stopped, the CR done.

Configured in `[notify]`: a `webhook` that gets a JSON POST (Slack incoming
webhooks accept the `text` field), and/or a `command` that gets the event as
JSON on stdin. Notifications are best-effort (an idea from ralphex): a failure
is reported once and never changes what the loop does.
"""

import json
import subprocess
import urllib.request

from ralph import ui
from ralph.config import NotifyConfig


class Notifier:
    def __init__(self, config: NotifyConfig, project: str):
        self.config = config
        self.project = project
        self._warned = False

    def send(self, event: str, message: str, **details) -> None:
        if event not in self.config.events or not (self.config.webhook or self.config.command):
            return
        payload = {"event": event, "project": self.project, "text": f"ralph · {self.project}: {message}", **details}
        try:
            if self.config.webhook:
                request = urllib.request.Request(self.config.webhook, data=json.dumps(payload).encode(),
                                                 headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(request, timeout=5).close()
            if self.config.command:
                subprocess.run(self.config.command, shell=True, input=json.dumps(payload), text=True,
                               timeout=10, capture_output=True, check=True)
        except Exception as problem:   # best-effort by design
            if not self._warned:
                ui.warn(f"Couldn't send a notification ({problem}); the loop carries on.")
                self._warned = True
