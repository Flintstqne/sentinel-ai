"""Test doubles shared by the monitoring tests."""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sentinel_ai.monitoring.model import (
    Assessment,
    Control,
    Evidence,
    Finding,
    ResolutionStatus,
    Result,
    Severity,
)
from sentinel_ai.monitoring.reader import CommandResult
from sentinel_ai.monitoring.store import ScanRecord

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=-4)))


class FakeReader:
    """An in-memory machine. Anything not provided behaves like a missing file or binary."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        commands: dict[str, tuple[int, str] | CommandResult] | None = None,
        unreadable: set[str] | None = None,
        mtimes: dict[str, datetime] | None = None,
        now: datetime = NOW,
    ) -> None:
        self.files = files or {}
        self.commands = commands or {}
        self.unreadable = unreadable or set()
        self.mtimes = mtimes or {}
        self._now = now
        self.ran: list[str] = []

    def read_text(self, path: str) -> str:
        if path in self.unreadable:
            raise PermissionError(13, "Permission denied")
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(2, "No such file")

    def read_tail(self, path: str, max_bytes: int = 0) -> str:
        return self.read_text(path)

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.unreadable

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatch(p, pattern))

    def mtime(self, path: str) -> datetime | None:
        return self.mtimes.get(path)

    def run(self, argv: Sequence[str]) -> CommandResult:
        key = " ".join(argv)
        self.ran.append(key)
        value = self.commands.get(key)
        if value is None:
            return CommandResult(127, "", f"{argv[0]}: not found")
        if isinstance(value, CommandResult):
            return value
        return CommandResult(value[0], value[1], "")

    def now(self) -> datetime:
        return self._now


CONTROL = Control("T-01", "A test control", "test reference")


def assessment(
    result: Result,
    severity: Severity = Severity.INFO,
    control_id: str = "T-01",
    check_id: str | None = None,
    evidence: bool = True,
) -> Assessment:
    return Assessment(
        check_id or control_id,
        Control(control_id, f"Control {control_id}", "ref"),
        result,
        severity,
        f"{result.value} explanation",
        (Evidence("src", "obs"),) if evidence else (),
        "do something" if result in (Result.FAIL, Result.WARNING) else None,
    )


def finding(
    severity: Severity = Severity.HIGH,
    result: Result = Result.FAIL,
    control_id: str = "T-01",
    status: ResolutionStatus = ResolutionStatus.OPEN,
) -> Finding:
    return Finding(
        f"F-{control_id}-0001",
        control_id,
        "host",
        severity,
        result,
        "2026-10-27T00:00:00Z",
        "2026-10-27T00:00:00Z",
        (Evidence("src", "obs"),),
        f"{control_id} explanation",
        "fix it",
        status,
    )


def record(
    assessments: tuple[Assessment, ...] = (),
    new: tuple[Finding, ...] = (),
    escalated: tuple[Finding, ...] = (),
    resolved: tuple[Finding, ...] = (),
    open_findings: tuple[Finding, ...] | None = None,
) -> ScanRecord:
    return ScanRecord(
        1,
        "host",
        "2026-10-27T00:00:00Z",
        "2026-10-27T00:00:05Z",
        assessments,
        new,
        escalated,
        resolved,
        (*new, *escalated) if open_findings is None else open_findings,
    )
