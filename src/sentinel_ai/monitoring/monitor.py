"""Runs scans off the UI thread and on a schedule.

The scan itself is a plain callable (`scan_fn() -> ScanRecord`) supplied by the app, so this
class knows nothing about checks or databases and can be tested with a fake. The callable
runs on a worker thread; results come back to the UI thread as queued Qt signals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from sentinel_ai.monitoring.store import ScanRecord

log = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 5
INITIAL_DELAY_MS = 10_000  # let the duck appear before the first scan starts


class _Signals(QObject):
    # Each carries the job so the receiver can release it. This object is created on the UI
    # thread, so Qt delivers these emissions there even though they are emitted from the worker
    # (checked empirically for bound methods, lambdas and plain functions in PySide6).
    finished = Signal(object, object)  # job, ScanRecord
    failed = Signal(object, str)  # job, message


class _ScanJob(QRunnable):
    def __init__(self, scan_fn: Callable[[], ScanRecord]) -> None:
        super().__init__()
        self.signals = _Signals()
        self._scan_fn = scan_fn

    def run(self) -> None:
        try:
            record = self._scan_fn()
        except Exception as exc:  # noqa: BLE001 - report to the UI thread instead of dying
            log.exception("scan failed")
            self.signals.failed.emit(self, f"{type(exc).__name__}: {exc}")
            return
        self.signals.finished.emit(self, record)


class Monitor(QObject):
    scan_started = Signal()
    scan_finished = Signal(object)  # ScanRecord
    scan_failed = Signal(str)
    state_changed = Signal()  # running / paused changed; lets the UI refresh its actions

    def __init__(
        self,
        scan_fn: Callable[[], ScanRecord],
        interval_minutes: int,
        initial_delay_ms: int = INITIAL_DELAY_MS,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._scan_fn = scan_fn
        self._pool = pool or QThreadPool.globalInstance()
        self._running = False
        self._paused = False
        self._interval_ms = 0
        self._jobs: set[_ScanJob] = set()  # keep signal objects alive until delivery

        self._initial = QTimer(self)
        self._initial.setSingleShot(True)
        self._initial.setInterval(initial_delay_ms)
        self._initial.timeout.connect(self.run_now)

        self._periodic = QTimer(self)
        self._periodic.timeout.connect(self.run_now)
        self.set_interval(interval_minutes)

    @property
    def interval_minutes(self) -> int:
        return self._interval_ms // 60_000

    # -- state ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def can_run(self) -> bool:
        return not self._running and not self._paused

    def set_interval(self, minutes: int) -> None:
        """0 means manual scans only; anything else is clamped to a sane minimum."""
        self._interval_ms = 0 if minutes <= 0 else max(minutes, MIN_INTERVAL_MINUTES) * 60_000
        self._arm()

    def _arm(self) -> None:
        """(Re)start the periodic timer only when it should run: never at interval 0."""
        self._periodic.stop()
        if self._interval_ms > 0 and not self._paused:
            self._periodic.start(self._interval_ms)

    def start(self) -> None:
        if self._paused:
            return
        self._arm()
        self._initial.start()

    def pause(self) -> None:
        self._paused = True
        self._initial.stop()
        self._periodic.stop()
        self.state_changed.emit()

    def resume(self) -> None:
        self._paused = False
        self._arm()
        self.state_changed.emit()

    # -- scanning ------------------------------------------------------------
    def run_now(self) -> bool:
        """Start a scan unless one is running or monitoring is paused. True if started."""
        if not self.can_run:
            return False
        self._running = True
        job = _ScanJob(self._scan_fn)
        job.setAutoDelete(False)
        job.signals.finished.connect(self._done)
        job.signals.failed.connect(self._failed)
        self._jobs.add(job)
        self.scan_started.emit()
        self.state_changed.emit()
        self._pool.start(job)
        return True

    def _done(self, job: _ScanJob, record: ScanRecord) -> None:
        self._jobs.discard(job)
        self._running = False
        self.state_changed.emit()
        self.scan_finished.emit(record)

    def _failed(self, job: _ScanJob, message: str) -> None:
        self._jobs.discard(job)
        self._running = False
        self.state_changed.emit()
        self.scan_failed.emit(message)
