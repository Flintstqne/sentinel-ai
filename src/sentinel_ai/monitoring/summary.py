"""Presentation-neutral summaries of a scan: labels, ordering, the lead sentence.

Shared by the UI and the exported reports, so both say the same thing about the same scan.
Pure Python, no Qt.
"""

from __future__ import annotations

from sentinel_ai.monitoring.model import Assessment, Result, Severity
from sentinel_ai.monitoring.store import ScanRecord

RESULT_LABEL = {
    Result.PASS: "Passing",
    Result.WARNING: "Warning",
    Result.FAIL: "Failing",
    Result.UNKNOWN: "Not assessed",
    Result.ERROR: "Check error",
}
SEVERITY_LABEL = {s: s.name.title() for s in Severity}
_RESULT_ORDER = {
    Result.FAIL: 0,
    Result.WARNING: 1,
    Result.ERROR: 2,
    Result.UNKNOWN: 3,
    Result.PASS: 4,
}


def sort_key(a: Assessment) -> tuple[int, int, str]:
    """Worst first: failures, warnings, errors, unassessed, then passes; severity within."""
    return (_RESULT_ORDER[a.result], -int(a.severity), a.control.control_id)


def sorted_assessments(record: ScanRecord) -> list[Assessment]:
    return sorted(record.assessments, key=sort_key)


def headline(record: ScanRecord | None) -> tuple[Result | None, str, str]:
    """(icon result, title, subtitle) for the dashboard's lead statement."""
    if record is None:
        return None, "No assessment yet", "Run a scan to see how this machine measures up."
    ordered = sorted_assessments(record)
    failing = [a for a in ordered if a.result is Result.FAIL]
    warning = [a for a in ordered if a.result is Result.WARNING]
    unassessed = [a for a in ordered if a.result in (Result.UNKNOWN, Result.ERROR)]
    if failing:
        n = len(failing)
        title = f"{n} control{'s' if n != 1 else ''} failing"
        result, lead, others = Result.FAIL, failing[0], len(warning)
    elif warning:
        n = len(warning)
        title = f"{n} warning{'s' if n != 1 else ''}"
        result, lead, others = Result.WARNING, warning[0], 0
    else:
        result = lead = None
    if result is not None and lead is not None:
        more = f" and {others} other issue{'s' if others != 1 else ''}" if others else ""
        return result, title, f"Most urgent: {lead.control.title}{more}."
    if unassessed:
        n = len(unassessed)
        return (
            Result.UNKNOWN,
            f"Nothing failing, {n} control{'s' if n != 1 else ''} not assessed",
            "Some evidence could not be read, so those controls are not counted as passing.",
        )
    n = len(ordered)
    checked = f"{n} control{'s' if n != 1 else ''} checked"
    return Result.PASS, "All checks passing", f"{checked}; nothing needs attention."
