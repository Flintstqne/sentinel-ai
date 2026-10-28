"""SQLite persistence for assessments and findings. Standard library only.

Two tables answer two different audit questions:
* `assessments`: the outcome of every control in every scan (the evidence trail, including
  passes), so "was this checked, and what did it show on that date?" is always answerable.
* `findings`: de-duplicated open issues with a lifecycle (open, acknowledged, resolved).

Lifecycle rules, all driven by evidence:
* A FAIL or WARNING opens a finding, or refreshes the existing open one.
* A PASS resolves an open or acknowledged finding for that control.
* UNKNOWN and ERROR change nothing: a check that could not run must never close a finding.
* A finding whose severity rises is reopened if it had been acknowledged.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentinel_ai.monitoring.model import (
    Assessment,
    Control,
    Evidence,
    Finding,
    ResolutionStatus,
    Result,
    Severity,
)

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
    scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
    check_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    control_title TEXT NOT NULL,
    reference TEXT NOT NULL,
    result TEXT NOT NULL,
    severity INTEGER NOT NULL,
    explanation TEXT NOT NULL,
    remediation TEXT,
    evidence TEXT NOT NULL,
    PRIMARY KEY (scan_id, check_id)
);
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    severity INTEGER NOT NULL,
    result TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    evidence TEXT NOT NULL,
    explanation TEXT NOT NULL,
    remediation TEXT,
    resolution_status TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS findings_by_control ON findings(host_id, control_id, resolution_status);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_json(evidence: tuple[Evidence, ...]) -> str:
    return json.dumps([{"source": e.source, "observation": e.observation} for e in evidence])


def _evidence_from(raw: str) -> tuple[Evidence, ...]:
    return tuple(Evidence(d["source"], d["observation"]) for d in json.loads(raw))


@dataclass(frozen=True)
class ScanRecord:
    scan_id: int
    host_id: str
    started_at: str
    finished_at: str
    assessments: tuple[Assessment, ...]
    new_findings: tuple[Finding, ...]
    escalated: tuple[Finding, ...]
    resolved: tuple[Finding, ...]
    open_findings: tuple[Finding, ...]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._tx():
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    # -- writing -------------------------------------------------------------
    def record_scan(
        self,
        host_id: str,
        assessments: tuple[Assessment, ...],
        started: datetime,
        finished: datetime | None = None,
    ) -> ScanRecord:
        finished = finished or utc_now()
        new: list[Finding] = []
        escalated: list[Finding] = []
        resolved: list[Finding] = []
        stamp = iso(finished)
        with self._tx() as db:
            scan_id = db.execute(
                "INSERT INTO scans (host_id, started_at, finished_at) VALUES (?, ?, ?)",
                (host_id, iso(started), stamp),
            ).lastrowid
            for a in assessments:
                db.execute(
                    "INSERT INTO assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scan_id,
                        a.check_id,
                        a.control.control_id,
                        a.control.title,
                        a.control.reference,
                        a.result.value,
                        int(a.severity),
                        a.explanation,
                        a.remediation,
                        _evidence_json(a.evidence),
                    ),
                )
                existing = db.execute(
                    "SELECT * FROM findings WHERE host_id = ? AND control_id = ? "
                    "AND resolution_status != 'resolved'",
                    (host_id, a.control.control_id),
                ).fetchone()
                if a.is_issue:
                    if existing is None:
                        finding = self._insert_finding(db, host_id, a, stamp, scan_id)
                        new.append(finding)
                    else:
                        status = existing["resolution_status"]
                        rose = int(a.severity) > existing["severity"]
                        if rose and status == "acknowledged":
                            status = "open"
                        db.execute(
                            "UPDATE findings SET severity=?, result=?, last_seen_at=?, evidence=?, "
                            "explanation=?, remediation=?, resolution_status=? WHERE finding_id=?",
                            (
                                int(a.severity),
                                a.result.value,
                                stamp,
                                _evidence_json(a.evidence),
                                a.explanation,
                                a.remediation,
                                status,
                                existing["finding_id"],
                            ),
                        )
                        if rose:
                            escalated.append(self._finding(db, existing["finding_id"]))
                elif a.result is Result.PASS and existing is not None:
                    db.execute(
                        "UPDATE findings SET resolution_status='resolved', resolved_at=?, "
                        "last_seen_at=? WHERE finding_id=?",
                        (stamp, stamp, existing["finding_id"]),
                    )
                    resolved.append(self._finding(db, existing["finding_id"]))
            open_now = self._open_findings(db, host_id)
        return ScanRecord(
            scan_id or 0,
            host_id,
            iso(started),
            stamp,
            assessments,
            tuple(new),
            tuple(escalated),
            tuple(resolved),
            open_now,
        )

    def _insert_finding(
        self, db: sqlite3.Connection, host_id: str, a: Assessment, stamp: str, scan_id: int | None
    ) -> Finding:
        # Deterministic and readable: control, the scan it first appeared in.
        finding_id = f"F-{a.control.control_id}-{scan_id:04d}"
        db.execute(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL)",
            (
                finding_id,
                host_id,
                a.control.control_id,
                int(a.severity),
                a.result.value,
                stamp,
                stamp,
                _evidence_json(a.evidence),
                a.explanation,
                a.remediation,
            ),
        )
        return self._finding(db, finding_id)

    def acknowledge(self, finding_id: str) -> bool:
        """The user has seen this and accepts it. Evidence still wins: a later PASS resolves it."""
        with self._tx() as db:
            cur = db.execute(
                "UPDATE findings SET resolution_status='acknowledged' "
                "WHERE finding_id=? AND resolution_status='open'",
                (finding_id,),
            )
            return cur.rowcount == 1

    # -- reading -------------------------------------------------------------
    @staticmethod
    def _row_to_finding(row: sqlite3.Row) -> Finding:
        return Finding(
            finding_id=row["finding_id"],
            control_id=row["control_id"],
            host_id=row["host_id"],
            severity=Severity(row["severity"]),
            result=Result(row["result"]),
            detected_at=row["detected_at"],
            last_seen_at=row["last_seen_at"],
            evidence=_evidence_from(row["evidence"]),
            explanation=row["explanation"],
            remediation=row["remediation"],
            resolution_status=ResolutionStatus(row["resolution_status"]),
            resolved_at=row["resolved_at"],
        )

    def _finding(self, db: sqlite3.Connection, finding_id: str) -> Finding:
        row = db.execute("SELECT * FROM findings WHERE finding_id = ?", (finding_id,)).fetchone()
        return self._row_to_finding(row)

    def _open_findings(self, db: sqlite3.Connection, host_id: str) -> tuple[Finding, ...]:
        rows = db.execute(
            "SELECT * FROM findings WHERE host_id = ? AND resolution_status != 'resolved' "
            "ORDER BY severity DESC, detected_at",
            (host_id,),
        ).fetchall()
        return tuple(self._row_to_finding(r) for r in rows)

    def open_findings(self, host_id: str) -> tuple[Finding, ...]:
        return self._open_findings(self._conn, host_id)

    def all_findings(self, host_id: str) -> tuple[Finding, ...]:
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE host_id = ? ORDER BY detected_at DESC", (host_id,)
        ).fetchall()
        return tuple(self._row_to_finding(r) for r in rows)

    def latest_scan(self, host_id: str) -> ScanRecord | None:
        """The most recent scan as stored (no change lists: those exist only at scan time)."""
        row = self._conn.execute(
            "SELECT * FROM scans WHERE host_id = ? ORDER BY scan_id DESC LIMIT 1", (host_id,)
        ).fetchone()
        if row is None:
            return None
        assessments = []
        for r in self._conn.execute(
            "SELECT * FROM assessments WHERE scan_id = ? ORDER BY rowid", (row["scan_id"],)
        ):
            assessments.append(
                Assessment(
                    r["check_id"],
                    Control(r["control_id"], r["control_title"], r["reference"]),
                    Result(r["result"]),
                    Severity(r["severity"]),
                    r["explanation"],
                    _evidence_from(r["evidence"]),
                    r["remediation"],
                )
            )
        return ScanRecord(
            row["scan_id"],
            host_id,
            row["started_at"],
            row["finished_at"],
            tuple(assessments),
            (),
            (),
            (),
            self.open_findings(host_id),
        )
