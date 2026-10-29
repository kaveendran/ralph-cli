"""Deterministic checks on what an agent changed, before any model judges it.

A unit fails if it deleted test files, added skip or xfail markers, or
touched a frozen path. Removed assertions are flagged for the reviewer
rather than failed, since refactoring a test can legitimately remove one.
"""

import fnmatch
import re
from dataclasses import dataclass, field

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")
SKIP_MARKER = re.compile(
    r"@pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail)\(|\bunittest\.skip|@skip\b"
    r"|\b(it|test|describe)\.(skip|todo)\(|\bx(it|describe|test)\(|\.only\(")
ASSERTION = re.compile(r"\bassert\b|\bexpect\(|\.assert[A-Z_]\w*\(|self\.assert\w*\(")


@dataclass
class IntegrityReport:
    failures: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def check_changes(changes: list[tuple[str, str]], diff_text: str, frozen: list[str]) -> IntegrityReport:
    """`changes` are (status, path) pairs (git's A/M/D/R codes); `diff_text` is a
    unified diff of the same changes."""
    report = IntegrityReport()
    for status, path in changes:
        if status.startswith("D") and is_test_path(path):
            report.failures.append(f"deleted test file {path}")
        hit = next((p for p in frozen if fnmatch.fnmatch(path, p)), None)
        if hit:
            report.failures.append(f"changed frozen path {path} ({hit})")

    current, removed_asserts = None, {}
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if not current or not is_test_path(current) or line.startswith(("---", "+++")):
            continue
        if line.startswith("+") and SKIP_MARKER.search(line):
            report.failures.append(f"added a skip/xfail/only marker in {current}: {line[1:].strip()[:100]}")
        elif line.startswith("-") and ASSERTION.search(line):
            removed_asserts[current] = removed_asserts.get(current, 0) + 1
    for path, count in removed_asserts.items():
        report.flags.append(f"{count} assertion line(s) removed from {path}")
    return report
