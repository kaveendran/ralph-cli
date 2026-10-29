"""Read the markdown reports the workflows write.

A plan critique puts severities under `###` headings and a deploy critique
under `##` ones, so a section ends at the next heading of the same or a
higher level, whatever level it started at.
"""

import re

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S")
TODO = re.compile(r"<TODO:[^>]*>")


def section(markdown: str, starts_with: str) -> str:
    """The body of the first heading whose text starts with `starts_with` (case-insensitive)."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match and match.group(2).strip().upper().startswith(starts_with.upper()):
            level = len(match.group(1))
            body = []
            for following in lines[index + 1:]:
                heading = HEADING.match(following)
                if heading and len(heading.group(1)) <= level:
                    break
                body.append(following)
            return "\n".join(body).strip()
    return ""


def count_items(markdown: str, severity: str) -> int:
    """Numbered items under the severity's heading ("None." counts as 0)."""
    return sum(1 for line in section(markdown, severity).splitlines() if NUMBERED.match(line))


def severities(markdown: str) -> dict[str, int]:
    return {name: count_items(markdown, name) for name in ("CRITICAL", "IMPORTANT", "SUGGESTED")}


def split_at(markdown: str, headings: list[str]) -> dict[str, str]:
    """Split a report at known top-level headings, in the order given.

    Each part runs to the next known heading, not the next heading of any
    kind, so a part may contain headings of its own (a PR body has `## Summary`).
    """
    lines = markdown.splitlines()
    starts = []
    for heading in headings:
        index = next((i for i, line in enumerate(lines) if line.strip().lower() == heading.lower()), None)
        if index is not None:
            starts.append((index, heading))
    starts.sort()
    parts = {}
    for position, (index, heading) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        parts[heading] = "\n".join(lines[index + 1:end]).strip()
    return parts


def todo_count(texts: list[str]) -> int:
    return sum(len(TODO.findall(text)) for text in texts)
