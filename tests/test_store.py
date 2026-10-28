from datetime import timedelta
from pathlib import Path

import pytest

from fakes import assessment
from sentinel_ai.monitoring.model import ResolutionStatus, Result, Severity
from sentinel_ai.monitoring.store import Store, utc_now

HOST = "host-1"


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


def scan(store: Store, *assessments, host: str = HOST):
    return store.record_scan(host, tuple(assessments), utc_now() - timedelta(seconds=2))


def test_a_fail_opens_a_finding_with_the_audit_fields(store: Store) -> None:
    rec = scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    (f,) = rec.new_findings
    assert f.finding_id == "F-FW-01-0001" and f.control_id == "FW-01" and f.host_id == HOST
    assert f.severity is Severity.HIGH and f.result is Result.FAIL
    assert f.resolution_status is ResolutionStatus.OPEN and f.resolved_at is None
    assert f.detected_at.endswith("Z") and f.evidence and f.explanation and f.remediation


def test_a_repeat_updates_the_finding_instead_of_duplicating_it(store: Store) -> None:
    scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    again = scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    assert again.new_findings == () and again.escalated == ()
    assert len(store.all_findings(HOST)) == 1 and len(again.open_findings) == 1


def test_a_pass_resolves_an_open_finding_and_a_pass_alone_creates_none(store: Store) -> None:
    scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    rec = scan(store, assessment(Result.PASS, Severity.INFO, "FW-01"))
    (done,) = rec.resolved
    assert done.resolution_status is ResolutionStatus.RESOLVED and done.resolved_at
    assert rec.open_findings == ()
    clean = scan(store, assessment(Result.PASS, Severity.INFO, "SSH-01"))
    assert clean.new_findings == () and len(store.all_findings(HOST)) == 1


@pytest.mark.parametrize("result", [Result.UNKNOWN, Result.ERROR])
def test_an_unassessable_check_never_closes_a_finding(store: Store, result) -> None:
    scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    rec = scan(store, assessment(result, Severity.INFO, "FW-01"))
    assert rec.resolved == () and len(rec.open_findings) == 1
    assert rec.new_findings == ()  # and it does not invent a new finding either


def test_every_scan_keeps_passes_as_audit_evidence(store: Store) -> None:
    scan(store, assessment(Result.PASS, control_id="A"), assessment(Result.FAIL, Severity.LOW, "B"))
    latest = store.latest_scan(HOST)
    assert {a.control.control_id: a.result for a in latest.assessments} == {
        "A": Result.PASS,
        "B": Result.FAIL,
    }
    assert all(a.evidence for a in latest.assessments if a.result is Result.PASS)


def test_severity_changes_are_tracked_and_escalation_is_reported(store: Store) -> None:
    scan(store, assessment(Result.WARNING, Severity.MEDIUM, "NET-01"))
    up = scan(store, assessment(Result.FAIL, Severity.HIGH, "NET-01"))
    (f,) = up.escalated
    assert f.severity is Severity.HIGH and f.result is Result.FAIL
    down = scan(store, assessment(Result.WARNING, Severity.LOW, "NET-01"))
    assert down.escalated == () and down.open_findings[0].severity is Severity.LOW


def test_acknowledged_findings_stay_acknowledged_until_they_get_worse(store: Store) -> None:
    (f,) = scan(store, assessment(Result.WARNING, Severity.MEDIUM, "NET-01")).new_findings
    assert store.acknowledge(f.finding_id) is True
    assert store.acknowledge(f.finding_id) is False  # already acknowledged
    same = scan(store, assessment(Result.WARNING, Severity.MEDIUM, "NET-01"))
    assert same.open_findings[0].resolution_status is ResolutionStatus.ACKNOWLEDGED
    worse = scan(store, assessment(Result.FAIL, Severity.HIGH, "NET-01"))
    assert worse.open_findings[0].resolution_status is ResolutionStatus.OPEN


def test_evidence_beats_acknowledgement(store: Store) -> None:
    (f,) = scan(store, assessment(Result.WARNING, Severity.MEDIUM, "NET-01")).new_findings
    store.acknowledge(f.finding_id)
    rec = scan(store, assessment(Result.PASS, control_id="NET-01"))
    assert len(rec.resolved) == 1 and rec.open_findings == ()


def test_a_recurrence_after_resolution_is_a_new_finding(store: Store) -> None:
    scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    scan(store, assessment(Result.PASS, control_id="FW-01"))
    back = scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    (f,) = back.new_findings
    assert f.finding_id == "F-FW-01-0003" and len(store.all_findings(HOST)) == 2


def test_hosts_are_kept_apart(store: Store) -> None:
    scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"), host="a")
    rec = scan(store, assessment(Result.FAIL, Severity.HIGH, "FW-01"), host="b")
    assert len(rec.new_findings) == 1
    assert len(store.open_findings("a")) == len(store.open_findings("b")) == 1


def test_data_survives_closing_and_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = Store(path)
    scan(first, assessment(Result.FAIL, Severity.HIGH, "FW-01"))
    first.close()
    second = Store(path)
    try:
        assert [f.control_id for f in second.open_findings(HOST)] == ["FW-01"]
        assert second.latest_scan(HOST).assessments[0].result is Result.FAIL
        assert second.latest_scan("nobody") is None
    finally:
        second.close()


def test_open_findings_are_ordered_most_severe_first(store: Store) -> None:
    scan(
        store,
        assessment(Result.WARNING, Severity.LOW, "A"),
        assessment(Result.FAIL, Severity.HIGH, "B"),
        assessment(Result.WARNING, Severity.MEDIUM, "C"),
    )
    assert [f.control_id for f in store.open_findings(HOST)] == ["B", "C", "A"]
