import threading

import pytest
from PySide6.QtCore import QObject, QThread, Slot
from PySide6.QtTest import QSignalSpy, QTest

from fakes import assessment, finding, record
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.monitoring.monitor import MIN_INTERVAL_MINUTES, Monitor

REC = record((assessment(Result.FAIL, Severity.HIGH),), new=(finding(),))


def wait_until(condition, timeout_ms: int = 3000) -> bool:
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    return condition()


class Recorder(QObject):
    """A receiver that notes which thread each delivery happened on."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list = []
        self.threads: list = []
        self.failures: list[str] = []

    @Slot(object)
    def finished(self, rec) -> None:
        self.records.append(rec)
        self.threads.append(QThread.currentThread())

    @Slot(str)
    def failed(self, message: str) -> None:
        self.failures.append(message)


def make(scan_fn, interval: int = 0, delay: int = 60_000) -> tuple[Monitor, Recorder]:
    monitor = Monitor(scan_fn, interval, initial_delay_ms=delay)
    rec = Recorder()
    monitor.scan_finished.connect(rec.finished)
    monitor.scan_failed.connect(rec.failed)
    return monitor, rec


def test_scan_runs_off_the_ui_thread_and_is_delivered_on_it(qapp) -> None:
    ran_on: list[int] = []

    def scan():
        ran_on.append(threading.get_ident())
        return REC

    monitor, rec = make(scan)
    started = QSignalSpy(monitor.scan_started)
    assert monitor.run_now()
    assert wait_until(lambda: rec.records)
    assert ran_on[0] != threading.get_ident(), "the scan blocked the UI thread"
    assert rec.threads[0] is qapp.thread(), "the result was delivered on the worker thread"
    assert rec.records[0] is REC and started.count() == 1
    assert not monitor.running


def test_only_one_scan_runs_at_a_time(qapp) -> None:
    gate = threading.Event()

    def scan():
        gate.wait(3)
        return REC

    monitor, rec = make(scan)
    assert monitor.run_now() and monitor.running and not monitor.can_run
    assert monitor.run_now() is False  # refused while one is running
    gate.set()
    assert wait_until(lambda: rec.records)
    assert len(rec.records) == 1 and monitor.can_run
    gate.clear()
    gate.set()
    assert monitor.run_now()  # and a new one is fine afterwards
    assert wait_until(lambda: len(rec.records) == 2)


def test_a_crashing_scan_is_reported_and_the_monitor_recovers(qapp) -> None:
    calls = []

    def scan():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk on fire")
        return REC

    monitor, rec = make(scan)
    monitor.run_now()
    assert wait_until(lambda: rec.failures)
    assert "disk on fire" in rec.failures[0] and not monitor.running
    monitor.run_now()
    assert wait_until(lambda: rec.records)


def test_pausing_blocks_scans_and_resuming_allows_them(qapp) -> None:
    monitor, rec = make(lambda: REC)
    changed = QSignalSpy(monitor.state_changed)
    monitor.pause()
    assert monitor.paused and not monitor.can_run and monitor.run_now() is False
    monitor.resume()
    assert not monitor.paused and monitor.can_run and changed.count() == 2
    assert monitor.run_now() and wait_until(lambda: rec.records)


def test_interval_zero_never_arms_the_periodic_timer(qapp) -> None:
    """Regression: a zero-interval QTimer fires continuously, which would scan in a loop."""
    monitor, _ = make(lambda: REC, interval=0)
    monitor.start()
    assert not monitor._periodic.isActive()
    monitor.pause()
    monitor.resume()
    assert not monitor._periodic.isActive() and monitor.interval_minutes == 0


def test_interval_is_clamped_and_arms_the_timer_in_milliseconds(qapp) -> None:
    monitor, _ = make(lambda: REC, interval=1)
    assert monitor.interval_minutes == MIN_INTERVAL_MINUTES
    monitor.set_interval(30)
    monitor.start()
    assert monitor._periodic.isActive() and monitor._periodic.interval() == 30 * 60_000
    monitor.pause()
    assert not monitor._periodic.isActive()
    monitor.resume()
    assert monitor._periodic.isActive()


def test_start_schedules_a_first_scan_unless_paused(qapp) -> None:
    monitor, rec = make(lambda: REC, delay=20)
    monitor.start()
    assert wait_until(lambda: rec.records), "no automatic first scan"
    paused, prec = make(lambda: REC, delay=20)
    paused.pause()
    paused.start()
    QTest.qWait(150)
    assert prec.records == []


@pytest.mark.parametrize("minutes", [-5, 0])
def test_non_positive_intervals_mean_manual_only(qapp, minutes) -> None:
    monitor, _ = make(lambda: REC, interval=minutes)
    assert monitor.interval_minutes == 0


def test_completion_handler_runs_on_the_ui_thread_not_the_worker(qapp, monkeypatch) -> None:
    """The handler touches timers and UI state, so it must run on the UI thread."""
    seen: list[int] = []
    original = Monitor._done

    def spy(self, job, rec):
        if self is target:  # other tests' monitors may still be finishing; ignore them
            seen.append(threading.get_ident())
        original(self, job, rec)

    monkeypatch.setattr(Monitor, "_done", spy)
    monitor, rec = make(lambda: REC)
    target = monitor
    monitor.run_now()
    assert wait_until(lambda: rec.records)
    assert seen == [threading.get_ident()]
