"""Text helpers for the UI. Pure Python, no Qt, fully testable.

Everything that ends up in a rich-text label goes through escape(): evidence can contain
attacker-controlled strings (a failed SSH login for the user name `<img src=x>` lands in
the auth log), so nothing from a scan is ever interpreted as markup.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

# Shared with the exported reports; re-exported here so the UI has one place to import from.
from sentinel_ai.monitoring.summary import RESULT_LABEL as RESULT_LABEL
from sentinel_ai.monitoring.summary import SEVERITY_LABEL as SEVERITY_LABEL
from sentinel_ai.monitoring.summary import headline as headline
from sentinel_ai.monitoring.summary import sort_key as sort_key
from sentinel_ai.monitoring.summary import sorted_assessments as sorted_assessments


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def parse_utc(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def relative(stamp: str, now: datetime | None = None) -> str:
    """'just now', '3 min ago', '2 h ago', '5 days ago'."""
    seconds = int(((now or datetime.now(UTC)) - parse_utc(stamp)).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min ago"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h ago"
    days = round(seconds / 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def local_clock(stamp: str) -> str:
    """The UTC timestamp in the user's local time, e.g. 'Oct 27, 01:28'."""
    local = parse_utc(stamp).astimezone()
    return f"{local:%b} {local.day}, {local:%H:%M}"


def rich_commands(text: str) -> str:
    """Escape text and render `backtick` spans as inline code (safe HTML)."""
    parts = text.split("`")
    out = []
    for i, part in enumerate(parts):
        safe = escape(part)
        out.append(f'<span style="font-family: monospace;"><b>{safe}</b></span>' if i % 2 else safe)
    return "".join(out)
