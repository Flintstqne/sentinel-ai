"""Runs every check and records the outcome. Qt-free, so it runs in a worker or a CLI."""

from __future__ import annotations

import hashlib
import logging
import platform

from sentinel_ai.monitoring.checks import CHECKS, CheckSpec
from sentinel_ai.monitoring.model import Assessment, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader
from sentinel_ai.monitoring.store import ScanRecord, Store, utc_now

log = logging.getLogger(__name__)


def host_identity(reader: Reader) -> str:
    """Hostname plus a short hash of the machine-id (so a rename does not look like a new host)."""
    name = platform.node() or "unknown-host"
    try:
        machine_id = reader.read_text("/etc/machine-id").strip()
    except OSError:
        return name
    return f"{name}-{hashlib.sha256(machine_id.encode()).hexdigest()[:8]}"


def run_check(spec: CheckSpec, reader: Reader) -> Assessment:
    """Run one check. A crash becomes an ERROR, never a PASS and never silence."""
    try:
        return spec.run(reader)
    except Exception as exc:  # noqa: BLE001 - a broken check must not take the scan down
        log.exception("check crashed", extra={"check_id": spec.check_id})
        return Assessment(
            spec.check_id,
            spec.control,
            Result.ERROR,
            Severity.INFO,
            f"The check failed to run ({type(exc).__name__}: {exc}). This says nothing about "
            "the state of the system.",
            (Evidence("sentinel-ai", f"{type(exc).__name__}: {exc}"),),
        )


def run_scan(
    reader: Reader,
    store: Store,
    host_id: str | None = None,
    checks: tuple[CheckSpec, ...] = CHECKS,
) -> ScanRecord:
    started = utc_now()
    host = host_id or host_identity(reader)
    assessments = tuple(run_check(spec, reader) for spec in checks)
    record = store.record_scan(host, assessments, started)
    log.info(
        "scan complete",
        extra={
            "scan_id": record.scan_id,
            "results": {a.check_id: a.result.value for a in assessments},
            "new_findings": len(record.new_findings),
            "resolved": len(record.resolved),
        },
    )
    return record
