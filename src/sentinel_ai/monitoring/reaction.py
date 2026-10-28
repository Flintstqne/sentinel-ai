"""How a finished scan changes the companion and what the user is told. Pure, Qt-free.

Principles: react to what CHANGED, not to what merely still exists (an unchanged open
finding must not make the duck alarm every hour), and let only confirmed high-severity
failures go red.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel_ai.companion.state_machine import State, Tone
from sentinel_ai.monitoring.model import Assessment, Finding, Result, Severity
from sentinel_ai.monitoring.store import ScanRecord

MAX_NOTIFICATIONS = 3


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    severity: Severity


@dataclass(frozen=True)
class Reaction:
    state: State | None  # transient state to play after the scan; None = just settle to idle
    tone: Tone
    notifications: tuple[Notification, ...]


def _is_confirmed_high(finding: Finding) -> bool:
    return finding.result is Result.FAIL and finding.severity >= Severity.HIGH


def react(record: ScanRecord) -> Reaction:
    changed = (*record.new_findings, *record.escalated)
    if changed:
        if any(_is_confirmed_high(f) for f in changed):
            state, tone = State.ALERT, Tone.CRITICAL
        elif any(f.severity >= Severity.MEDIUM for f in changed):
            state, tone = State.ALERT, Tone.WARNING
        else:
            state, tone = State.INSIGHT, Tone.NORMAL
    elif not record.open_findings and any(a.result is Result.PASS for a in record.assessments):
        state, tone = State.HAPPY, Tone.NORMAL
    else:
        state, tone = None, Tone.NORMAL
    return Reaction(state, tone, _notifications(changed, record))


def _notifications(changed: tuple[Finding, ...], record: ScanRecord) -> tuple[Notification, ...]:
    titles = {a.control.control_id: a.control.title for a in record.assessments}
    ranked = sorted(changed, key=lambda f: -f.severity)
    out = [
        Notification(
            f"{f.severity.name.title()}: {titles.get(f.control_id, f.control_id)}",
            f.explanation,
            f.severity,
        )
        for f in ranked[:MAX_NOTIFICATIONS]
    ]
    extra = len(ranked) - MAX_NOTIFICATIONS
    if extra > 0:
        out.append(
            Notification(
                "Sentinel AI", f"{extra} more new findings. Open the dashboard.", Severity.INFO
            )
        )
    return tuple(out)


def summarise(assessments: tuple[Assessment, ...]) -> dict[str, int]:
    """Counts by result, straight from the assessments; nothing estimated."""
    counts = {r.value: 0 for r in Result}
    for a in assessments:
        counts[a.result.value] += 1
    return counts
