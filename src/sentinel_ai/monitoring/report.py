"""Plain-text rendering of a scan, for the terminal and the dashboard."""

from __future__ import annotations

from sentinel_ai.monitoring.model import Assessment, Finding, ResolutionStatus
from sentinel_ai.monitoring.reaction import summarise
from sentinel_ai.monitoring.store import ScanRecord

_BADGE = {"pass": "PASS", "fail": "FAIL", "warning": "WARN", "unknown": "UNKN", "error": "ERR "}


def _assessment(a: Assessment) -> list[str]:
    lines = [f"[{_BADGE[a.result.value]}] {a.control.control_id}  {a.control.title}"]
    lines.append(f"       {a.explanation}")
    for e in a.evidence:
        lines.append(f"       evidence: {e.source}: {e.observation}")
    if a.remediation:
        lines.append(f"       recommended: {a.remediation}")
    return lines


def _finding(f: Finding) -> str:
    flag = " (acknowledged)" if f.resolution_status is ResolutionStatus.ACKNOWLEDGED else ""
    return (
        f"  {f.finding_id}  {f.severity.name:<8} {f.result.value:<7} first seen "
        f"{f.detected_at}{flag}"
    )


def render_text(record: ScanRecord) -> str:
    counts = summarise(record.assessments)
    out = [
        f"Sentinel AI assessment, scan #{record.scan_id}",
        f"Host {record.host_id}; finished {record.finished_at}",
        "Read-only: nothing on this system was changed.",
        "",
        "  ".join(f"{k}: {v}" for k, v in counts.items()),
        "",
    ]
    for a in record.assessments:
        out.extend(_assessment(a))
        out.append("")
    out.append(f"Open findings ({len(record.open_findings)}):")
    if record.open_findings:
        out.extend(_finding(f) for f in record.open_findings)
    else:
        out.append("  none")
    return "\n".join(out) + "\n"
