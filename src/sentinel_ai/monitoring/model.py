"""Core vocabulary for security assessments. Pure Python, no Qt, no I/O.

Evidence holds only *observations* (what was read, from where). Everything interpretive
(explanation, severity, remediation) lives on the Assessment. Keeping them apart is what
lets an AI layer later explain a finding without being able to alter the facts under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class Result(Enum):
    PASS = "pass"  # evidence confirms the control is satisfied
    FAIL = "fail"  # evidence confirms the control is not satisfied
    WARNING = "warning"  # evidence shows a weakness or a condition worth reviewing
    UNKNOWN = "unknown"  # the evidence needed to decide could not be collected
    ERROR = "error"  # the check itself failed; says nothing about the system


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ResolutionStatus(Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class Control:
    control_id: str
    title: str
    reference: str  # illustrative mapping to a framework control, never a compliance claim


@dataclass(frozen=True)
class Evidence:
    source: str  # a file path or the command that was run
    observation: str  # what was seen there, verbatim or summarised; never an opinion


@dataclass(frozen=True)
class Assessment:
    check_id: str
    control: Control
    result: Result
    severity: Severity
    explanation: str
    evidence: tuple[Evidence, ...]
    remediation: str | None = None  # advice for a human; Sentinel never applies it

    def __post_init__(self) -> None:
        # The invariant the whole product rests on: no evidence, no PASS.
        if self.result is Result.PASS and not self.evidence:
            raise ValueError(f"{self.check_id}: a PASS needs at least one piece of evidence")

    @property
    def is_issue(self) -> bool:
        """FAIL and WARNING become findings; UNKNOWN and ERROR do not (nothing is confirmed)."""
        return self.result in (Result.FAIL, Result.WARNING)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    control_id: str
    host_id: str
    severity: Severity
    result: Result
    detected_at: str  # ISO 8601 UTC, when it was first seen
    last_seen_at: str
    evidence: tuple[Evidence, ...]
    explanation: str
    remediation: str | None
    resolution_status: ResolutionStatus
    resolved_at: str | None = None
