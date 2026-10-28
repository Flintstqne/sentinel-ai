from pathlib import Path

import pytest

from fakes import FakeReader, assessment
from sentinel_ai.monitoring.checks import CHECKS, CheckSpec
from sentinel_ai.monitoring.model import Control, Result
from sentinel_ai.monitoring.scanner import host_identity, run_check, run_scan
from sentinel_ai.monitoring.store import Store


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


def test_default_registry_covers_the_six_initial_checks_firewall_first() -> None:
    assert [c.check_id for c in CHECKS] == [
        "firewall",
        "ssh_exposure",
        "ssh_hardening",
        "auth_failures",
        "security_updates",
        "listening_ports",
    ]
    assert len({c.control.control_id for c in CHECKS}) == 6


def test_every_check_runs_read_only_on_an_empty_machine_without_crashing(store: Store) -> None:
    """A bare FakeReader has no files and no binaries: nothing may raise, nothing may PASS blindly."""
    rec = run_scan(FakeReader(), store, "h")
    assert len(rec.assessments) == 6
    by_id = {a.check_id: a for a in rec.assessments}
    assert by_id["firewall"].result is Result.WARNING
    assert by_id["auth_failures"].result is Result.UNKNOWN
    assert by_id["security_updates"].result is Result.UNKNOWN
    assert by_id["listening_ports"].result is Result.UNKNOWN
    assert all(a.result is not Result.ERROR for a in rec.assessments)


def test_a_crashing_check_becomes_an_error_not_a_pass_and_the_scan_continues(store: Store) -> None:
    control = Control("X-01", "Crashy", "ref")

    def boom(reader):
        raise RuntimeError("kaboom")

    specs = (
        CheckSpec("crashy", control, boom),
        CheckSpec("fine", control, lambda r: assessment(Result.PASS, check_id="fine")),
    )
    rec = run_scan(FakeReader(), store, "h", specs)
    crashed, fine = rec.assessments
    assert crashed.result is Result.ERROR and "kaboom" in crashed.explanation
    assert crashed.control.control_id == "X-01"
    assert "says nothing about the state of the system" in crashed.explanation
    assert fine.result is Result.PASS
    assert rec.new_findings == ()  # an ERROR is not a finding


def test_run_check_passes_through_normal_results() -> None:
    spec = CheckSpec("ok", Control("X", "t", "r"), lambda r: assessment(Result.FAIL))
    assert run_check(spec, FakeReader()).result is Result.FAIL


def test_scans_persist_and_findings_follow_the_evidence(store: Store) -> None:
    bad = FakeReader(
        files={"/etc/ufw/ufw.conf": "ENABLED=no\n"},
        commands={"systemctl is-active ufw": (0, "active\n")},
    )
    first = run_scan(bad, store, "h")
    assert [f.control_id for f in first.new_findings] == ["FW-01"]
    second = run_scan(bad, store, "h")
    assert second.new_findings == () and len(second.open_findings) == 1


def test_host_identity_hashes_the_machine_id() -> None:
    reader = FakeReader({"/etc/machine-id": "0123456789abcdef\n"})
    ident = host_identity(reader)
    assert "0123456789abcdef" not in ident and "-" in ident
    assert host_identity(reader) == ident
    assert (
        host_identity(FakeReader()) and "-" not in host_identity(FakeReader()).replace("_", "x")[:0]
    )
