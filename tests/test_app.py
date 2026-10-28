import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

import sentinel_ai.app as app_module
from fakes import FakeReader, assessment, finding, record
from sentinel_ai.companion.state_machine import State, Tone
from sentinel_ai.config import Config
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.monitoring.store import Store
from sentinel_ai.platform import x11
from sentinel_ai.ui.panels import NO_SCAN, status_lines

HIGH = record(
    (assessment(Result.FAIL, Severity.HIGH, "FW-01"), assessment(Result.PASS, control_id="B")),
    new=(finding(Severity.HIGH, Result.FAIL, "FW-01"),),
)
CLEAN = record((assessment(Result.PASS, control_id="A"),), open_findings=())


def wait_until(condition, timeout_ms: int = 3000) -> bool:
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    return condition()


class Env:
    """A SentinelApp wired to a fake scan and a temporary database."""

    def __init__(self, qapp, monkeypatch, tmp_path: Path, **cfg) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(app_module, "resolve_reduced_motion", lambda value: False)
        monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
        monkeypatch.setattr(app_module.SentinelApp, "tray_available", property(lambda s: True))
        self.config_path = tmp_path / "cfg" / "sentinel-ai" / "config.json"
        self.db = tmp_path / "data" / "sentinel.db"
        self.gate = threading.Event()
        self.gate.set()
        self.result = HIGH
        self.calls = 0
        self.fail_with: str | None = None
        self.shown: list[tuple] = []

        def scan():
            self.calls += 1
            self.gate.wait(3)
            if self.fail_with:
                raise RuntimeError(self.fail_with)
            return self.result

        cfg.setdefault("scan_interval_minutes", 0)  # manual only: no surprise background scans
        self.app = app_module.SentinelApp(qapp, Config(**cfg), self.db, scan)
        self.app.tray.showMessage = lambda *a: self.shown.append(a)

    @property
    def state(self) -> State:
        return self.app.companion.machine.state

    def run_scan(self) -> None:
        self.app._scan_action.trigger()
        assert wait_until(lambda: not self.app.monitor.running and self.calls > 0)
        QTest.qWait(30)  # let the queued finished signal reach the app


@pytest.fixture
def env(qapp, monkeypatch, tmp_path):
    e = Env(qapp, monkeypatch, tmp_path)
    yield e
    # A failing test may leave a gated scan running on a worker thread; let it finish so
    # the interpreter never exits with a live thread (that aborts the whole test run).
    e.gate.set()
    QThreadPool.globalInstance().waitForDone(5000)
    e.app.companion.close()


def test_menu_has_every_item_the_handoff_asks_for(env) -> None:
    labels = [a.text() for a in env.app.menu.actions() if a.text()]
    assert labels == [
        "Open dashboard",
        "Run scan",
        "Export report...",
        "Pause monitoring",
        "Walk around desktop",
        "View alerts",
        "Quit",
    ]


def test_run_scan_is_enabled_now_that_checks_exist(env) -> None:
    assert env.app._scan_action.isEnabled()
    assert "read-only" in env.app._scan_action.toolTip()


def test_a_confirmed_high_severity_finding_makes_the_duck_alert_red_and_notifies(env) -> None:
    env.run_scan()
    assert env.state is State.ALERT and env.app.companion.machine.tone is Tone.CRITICAL
    (shown,) = env.shown
    assert shown[0] == "High: Control FW-01" and shown[1] == "FW-01 explanation"
    assert env.app.last_record is HIGH
    assert env.app.dashboard.row_titles()[0] == "Control FW-01"  # worst control listed first
    assert env.app.dashboard.selected_check_id == "FW-01"


def test_the_duck_scans_while_working_then_reacts(env) -> None:
    env.gate.clear()
    env.app._scan_action.trigger()
    assert wait_until(lambda: env.state is State.SCANNING)
    assert env.app._scan_action.text() == "Scanning..." and not env.app._scan_action.isEnabled()
    env.gate.set()
    assert wait_until(lambda: env.state is State.ALERT)
    assert env.app._scan_action.text() == "Run scan" and env.app._scan_action.isEnabled()


def test_a_clean_scan_makes_the_duck_happy_without_notifications(env) -> None:
    env.result = CLEAN
    env.run_scan()
    assert wait_until(lambda: env.state is State.HAPPY)
    assert env.shown == []


def test_notifications_can_be_turned_off(qapp, monkeypatch, tmp_path) -> None:
    e = Env(qapp, monkeypatch, tmp_path, notifications=False)
    e.run_scan()
    assert e.shown == [] and e.state is State.ALERT  # still reacts, just silently
    e.app.companion.close()


def test_no_notifications_while_monitoring_is_paused(env) -> None:
    env.app._pause_action.setChecked(True)
    env.app._on_scan_finished(HIGH)
    assert env.shown == [] and env.state is State.SLEEPING


def test_a_failed_scan_returns_the_duck_to_idle_and_the_app_keeps_working(env) -> None:
    env.fail_with = "boom"
    env.run_scan()
    assert env.state is State.IDLE and env.app.last_record is None
    env.fail_with = None
    env.run_scan()
    assert wait_until(lambda: env.state is State.ALERT)


def test_pausing_disables_scanning_and_stops_the_duck_walking(env) -> None:
    env.app._pause_action.setChecked(True)
    assert not env.app._scan_action.isEnabled()
    assert env.app.monitor.run_now() is False and env.calls == 0
    assert json.loads(env.config_path.read_text())["monitoring_paused"] is True
    env.app._pause_action.setChecked(False)
    assert env.app._scan_action.isEnabled()


def test_walk_toggle_stops_the_duck_and_is_saved(env) -> None:
    env.app._roam_action.setChecked(False)
    assert not env.app.companion.roam
    assert json.loads(env.config_path.read_text())["roam"] is False


def test_status_panel_and_dashboard_say_so_before_any_scan(env) -> None:
    assert env.app.dashboard.row_titles() == []  # empty state, not made-up rows
    assert status_lines(None) == [NO_SCAN]
    env.app.status_panel.update_status(State.IDLE, False, None)
    assert NO_SCAN in env.app.status_panel._summary.text()


def test_status_lines_report_only_counts_from_the_assessments() -> None:
    rec = record(
        (
            assessment(Result.PASS, control_id="A"),
            assessment(Result.FAIL, Severity.HIGH, "B"),
            assessment(Result.WARNING, Severity.MEDIUM, "C"),
            assessment(Result.UNKNOWN, control_id="D"),
        ),
        open_findings=(finding(Severity.HIGH, Result.FAIL, "B"), finding(control_id="C")),
    )
    lines = status_lines(rec)
    assert "Passing 1, warnings 1, failing 1" in lines[1]
    assert "1 check(s) could not be assessed" in lines
    assert "Open findings: 2 (2 high)" in lines


def test_the_last_scan_is_restored_from_the_database_on_startup(
    qapp, monkeypatch, tmp_path
) -> None:
    first = Env(qapp, monkeypatch, tmp_path)
    store = Store(first.db)
    store.record_scan(
        first.app.host_id,
        (assessment(Result.FAIL, Severity.HIGH, "FW-01"),),
        datetime.now(UTC),
    )
    store.close()
    first.app.companion.close()
    second = Env(qapp, monkeypatch, tmp_path)
    assert second.app.last_record is not None
    assert second.app.dashboard.selected_check_id == "FW-01"
    second.app.companion.close()


def test_an_unusable_database_does_not_stop_the_app(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(app_module, "resolve_reduced_motion", lambda value: False)
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    app = app_module.SentinelApp(qapp, Config(scan_interval_minutes=0), tmp_path, lambda: HIGH)
    assert app.last_record is None  # db path is a directory: sqlite cannot open it
    app.companion.close()


def test_start_places_the_duck_on_its_saved_line_and_starts_monitoring(
    qapp, monkeypatch, tmp_path
) -> None:
    e = Env(qapp, monkeypatch, tmp_path, companion_x=120, companion_y=200)
    e.app.start()
    assert e.app.companion.x() == 120  # the saved horizontal position is kept
    assert e.app.companion.y() == e.app.companion.floor_y  # and it stands on the floor
    e.app.companion.close()


def test_scan_once_cli_prints_a_report_and_persists_it(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(app_module, "LocalReader", lambda: FakeReader())
    assert app_module.main(["sentinel-ai", "--scan"]) == 0
    out = capsys.readouterr().out
    assert "Sentinel AI assessment" in out and "Read-only" in out and "[WARN] FW-01" in out
    assert (tmp_path / "sentinel-ai" / "sentinel.db").exists()


def test_a_scan_with_nothing_new_settles_the_duck_back_to_idle(env) -> None:
    env.result = record(
        (assessment(Result.FAIL, Severity.HIGH, "FW-01"),),
        open_findings=(finding(Severity.HIGH, Result.FAIL, "FW-01"),),
    )
    env.run_scan()
    assert wait_until(lambda: env.state is State.IDLE)
    assert env.shown == []  # an unchanged, already-known finding does not re-notify


def test_constructing_the_app_raises_nothing_in_any_slot(qapp, monkeypatch, tmp_path) -> None:
    """Regression: setChecked() in __init__ fired a slot before self.companion existed.

    PySide swallows exceptions from slots and prints them, so the app 'worked' while
    spamming a traceback on every start. Collect them via sys.excepthook instead.
    """
    import sys

    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(v))
    for roam in (True, False):
        e = Env(qapp, monkeypatch, tmp_path / str(roam), roam=roam)
        QTest.qWait(20)
        e.app.companion.close()
    assert errors == []


def test_the_dashboard_scan_button_runs_a_scan_and_shows_progress(env) -> None:
    env.gate.clear()
    env.app.dashboard.scan_requested.emit()
    assert wait_until(lambda: env.app.dashboard._scanning)
    assert not env.app.dashboard._scan_button.isEnabled()
    env.gate.set()
    assert wait_until(lambda: not env.app.dashboard._scanning and env.app.last_record is not None)
    assert env.app.dashboard.row_titles()


def test_acknowledging_a_finding_persists_and_the_dashboard_reflects_it(env) -> None:
    env.run_scan()  # the fake scan result is not stored; write a real record for this host
    store = Store(env.db)
    rec = store.record_scan(
        env.app.host_id,
        (assessment(Result.FAIL, Severity.HIGH, "FW-01"),),
        datetime.now(UTC),
    )
    store.close()
    env.app.last_record = Store(env.db).latest_scan(env.app.host_id)
    env.app.dashboard.show_record(env.app.last_record)
    (finding_id,) = [f.finding_id for f in rec.new_findings]
    env.app.dashboard.acknowledge_requested.emit(finding_id)
    after = Store(env.db)
    try:
        assert after.open_findings(env.app.host_id)[0].resolution_status.value == "acknowledged"
    finally:
        after.close()
    assert "Acknowledged" in env.app.dashboard._rows[0].meta.text()


QUIT_SCRIPT = """
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
d = tempfile.mkdtemp()
for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
    os.environ[k] = d
import sentinel_ai.app as m
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

start = m.SentinelApp.start
def patched(self):
    start(self)
    self.show_dashboard()
    self.show_status()
    QTimer.singleShot(1000, QApplication.quit)
m.SentinelApp.start = patched
print("main returned", m.main(["sentinel-ai"]))
"""


def test_the_real_app_starts_shows_every_window_and_quits_cleanly() -> None:
    """Runs the genuine main() in a child process: a crash at exit (a Qt object destroyed
    after the QApplication) would be invisible to in-process tests."""
    import subprocess
    import sys

    for _ in range(3):
        proc = subprocess.run(
            [sys.executable, "-c", QUIT_SCRIPT], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        assert "main returned 0" in proc.stdout
        assert "Traceback" not in proc.stderr


# -- exporting -----------------------------------------------------------------------


def seed_real_scan(env) -> None:
    """Put a genuine stored scan in the database, as a finished run would have."""
    store = Store(env.db)
    store.record_scan(
        env.app.host_id,
        (assessment(Result.FAIL, Severity.HIGH, "FW-01"), assessment(Result.PASS, control_id="A")),
        datetime.now(UTC),
    )
    store.close()


def test_export_is_unavailable_until_something_was_scanned(env) -> None:
    assert not env.app._export_action.isEnabled()
    assert not env.app.dashboard._export_button.isVisibleTo(env.app.dashboard)
    assert env.app.export_to(env.db.parent / "r.html") is False
    assert not (env.db.parent / "r.html").exists()


def test_exporting_writes_an_owner_only_report_and_confirms_it(env) -> None:
    import stat

    seed_real_scan(env)
    target = env.db.parent / "out" / "report.html"
    assert env.app.export_to(target) is True
    text = target.read_text()
    assert 'id="FW-01"' in text and "Content SHA-256" in text
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert env.app.dashboard._when.text() == "Saved report.html"


def test_a_failed_export_tells_the_user_and_keeps_the_app_alive(env) -> None:
    seed_real_scan(env)
    assert env.app.export_to(env.db.parent / "report.txt") is False  # unknown format
    assert "Could not export" in env.app.dashboard._when.text()
    blocker = env.db.parent / "blocker"
    blocker.write_text("a file, not a folder")
    assert env.app.export_to(blocker / "r.json") is False  # cannot create the parent
    assert "Could not export" in env.app.dashboard._when.text()


def test_the_save_dialog_flow_picks_the_format_from_the_filter(env, monkeypatch) -> None:
    seed_real_scan(env)
    target = env.db.parent / "chosen"
    monkeypatch.setattr(
        app_module.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(target), "JSON data (*.json)"),
    )
    env.app._on_export_requested()
    assert json.loads((env.db.parent / "chosen.json").read_text())["schema"].startswith("sentinel")
    monkeypatch.setattr(app_module.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    env.app._on_export_requested()  # cancelled: nothing happens, nothing raises


def test_a_finished_scan_enables_the_export_menu_entry(env) -> None:
    env.run_scan()
    assert env.app._export_action.isEnabled()
    assert env.app.dashboard._export_button.isVisibleTo(env.app.dashboard)


def test_the_dashboard_export_button_asks_the_app_to_export(env, monkeypatch) -> None:
    asked = []
    monkeypatch.setattr(
        app_module.QFileDialog, "getSaveFileName", lambda *a, **k: asked.append(1) or ("", "")
    )
    env.run_scan()
    env.app.dashboard._export_button.click()
    assert asked == [1]


class FakeLocal:
    """app.LocalReader stand-in so the CLI never reads the real machine."""

    def __new__(cls):
        return FakeReader()


def cli(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(app_module, "LocalReader", FakeLocal)
    return app_module.main(["sentinel-ai", *args])


def test_cli_export_with_scan_writes_both_formats(monkeypatch, tmp_path, capsys) -> None:
    assert cli(monkeypatch, tmp_path, "--scan", "--export", str(tmp_path / "r.html")) == 0
    assert "Saved" in capsys.readouterr().out and (tmp_path / "r.html").exists()
    # without --scan it exports what is already stored, with no new scan
    assert cli(monkeypatch, tmp_path, "--export", str(tmp_path / "r.json")) == 0
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["scan"]["scan_id"] == 1 and data["summary"]["controls_total"] == 6


def test_cli_export_refuses_cleanly_when_there_is_nothing_to_export(monkeypatch, tmp_path, capsys):
    assert cli(monkeypatch, tmp_path, "--export", str(tmp_path / "r.html")) == 2
    assert "no scan has been recorded" in capsys.readouterr().err
    assert not (tmp_path / "r.html").exists()


def test_cli_export_validates_its_arguments(monkeypatch, tmp_path, capsys) -> None:
    assert cli(monkeypatch, tmp_path, "--export") == 2
    assert ".html or .json" in capsys.readouterr().err
    assert cli(monkeypatch, tmp_path, "--scan", "--export", str(tmp_path / "r.pdf")) == 2
    assert "cannot tell the format" in capsys.readouterr().err


def test_cli_export_reports_an_unwritable_location(monkeypatch, tmp_path, capsys) -> None:
    cli(monkeypatch, tmp_path, "--scan")  # make a scan available
    capsys.readouterr()
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    assert cli(monkeypatch, tmp_path, "--export", str(blocker / "r.html")) == 1
    assert "could not write the report" in capsys.readouterr().err
