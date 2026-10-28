"""Application wiring: companion, tray, panels, and the monitor that feeds them."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLockFile, QPoint, QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from sentinel_ai import __version__
from sentinel_ai.companion.render import canvas_size, tray_icon
from sentinel_ai.companion.state_machine import State
from sentinel_ai.companion.window import CompanionWindow, clamp_to_screens
from sentinel_ai.config import Config, data_dir
from sentinel_ai.logging_setup import configure_logging
from sentinel_ai.monitoring import export
from sentinel_ai.monitoring.model import Severity
from sentinel_ai.monitoring.monitor import Monitor
from sentinel_ai.monitoring.reaction import Notification, react
from sentinel_ai.monitoring.reader import LocalReader
from sentinel_ai.monitoring.report import render_text
from sentinel_ai.monitoring.scanner import host_identity, run_scan
from sentinel_ai.monitoring.store import ScanRecord, Store
from sentinel_ai.platform.motion import resolve_reduced_motion
from sentinel_ai.ui.dashboard import DashboardWindow
from sentinel_ai.ui.fonts import apply_app_font, load_fonts
from sentinel_ai.ui.icons import clear_pixmap_caches
from sentinel_ai.ui.panels import StatusPanel
from sentinel_ai.ui.theme import build_stylesheet

log = logging.getLogger(__name__)

NOTIFICATION_MS = 10_000
_TRAY_ICON = {
    Severity.INFO: QSystemTrayIcon.MessageIcon.Information,
    Severity.LOW: QSystemTrayIcon.MessageIcon.Information,
    Severity.MEDIUM: QSystemTrayIcon.MessageIcon.Warning,
    Severity.HIGH: QSystemTrayIcon.MessageIcon.Critical,
    Severity.CRITICAL: QSystemTrayIcon.MessageIcon.Critical,
}


def default_db_path() -> Path:
    return data_dir() / "sentinel.db"


class SentinelApp:
    def __init__(
        self,
        qapp: QApplication,
        config: Config,
        db_path: Path | None = None,
        scan_fn: Callable[[], ScanRecord] | None = None,
    ) -> None:
        self.config = config
        self.db_path = db_path or default_db_path()
        self.host_id = host_identity(LocalReader())
        self.last_record: ScanRecord | None = self._load_last_record()
        load_fonts()
        self.dashboard = DashboardWindow()
        self.dashboard.show_record(self.last_record)
        self.dashboard.scan_requested.connect(self._on_run_scan)
        self.dashboard.acknowledge_requested.connect(self._on_acknowledge)
        self.dashboard.export_requested.connect(self._on_export_requested)
        self.status_panel = StatusPanel()
        self.status_panel.open_dashboard_requested.connect(self.show_dashboard)

        self.menu = QMenu()
        self._open_action = self._action("Open dashboard", self.show_dashboard)
        self._scan_action = self._action("Run scan", self._on_run_scan)
        self._export_action = self._action("Export report...", self._on_export_requested)
        self._pause_action = self._action("Pause monitoring", self._on_pause_toggled, True)
        self._roam_action = self._action(
            "Walk around desktop", self._on_roam_toggled, True, checked=config.roam
        )
        self._alerts_action = self._action("View alerts", self.show_dashboard)
        self.menu.addActions(
            [
                self._open_action,
                self._scan_action,
                self._export_action,
                self._pause_action,
                self._roam_action,
                self._alerts_action,
            ]
        )
        self.menu.addSeparator()
        self.menu.addAction(self._action("Quit", qapp.quit))

        reduced = resolve_reduced_motion(config.reduced_motion)
        self.companion = CompanionWindow(config.scale, reduced, self.menu, roam=config.roam)
        self.companion.single_clicked.connect(self.show_status)
        self.companion.double_clicked.connect(self.show_dashboard)
        self.companion.moved_by_user.connect(self._remember_position)

        self.tray = QSystemTrayIcon(tray_icon(), qapp)
        self.tray.setToolTip(f"Sentinel AI {__version__}")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)

        self.monitor = Monitor(scan_fn or self._scan, config.scan_interval_minutes)
        self.monitor.scan_started.connect(self._on_scan_started)
        self.monitor.scan_finished.connect(self._on_scan_finished)
        self.monitor.scan_failed.connect(self._on_scan_failed)
        self.monitor.state_changed.connect(self._refresh_scan_action)
        self._refresh_scan_action()
        self._export_action.setEnabled(self.last_record is not None)

        log.info(
            "starting",
            extra={
                "reduced_motion": reduced,
                "tray_available": self.tray_available,
                "scan_interval_minutes": config.scan_interval_minutes,
                "host": self.host_id,
            },
        )

    # -- scanning ------------------------------------------------------------
    def _scan(self) -> ScanRecord:
        """Runs on a worker thread, so it opens its own database connection."""
        store = Store(self.db_path)
        try:
            return run_scan(LocalReader(), store, self.host_id)
        finally:
            store.close()

    def _load_last_record(self) -> ScanRecord | None:
        try:
            store = Store(self.db_path)
        except Exception:  # noqa: BLE001 - an unreadable database must not stop the app
            log.exception("could not open the findings database", extra={"path": str(self.db_path)})
            return None
        try:
            return store.latest_scan(self.host_id)
        finally:
            store.close()

    def _on_run_scan(self) -> None:
        self.monitor.run_now()

    def _on_export_requested(self) -> None:
        """Ask where to save, then write the latest scan as an audit evidence report."""
        folder = Path.home() / "Documents"
        name = export.default_filename(
            {"host": {"id": self.host_id}}, "html"
        )  # the dialog's filter below can switch it to JSON
        path, chosen = QFileDialog.getSaveFileName(
            self.dashboard,
            "Export audit evidence report",
            str(folder if folder.is_dir() else Path.home()) + "/" + name,
            "HTML report (*.html);;JSON data (*.json)",
        )
        if path:
            if export.kind_for(path) is None:  # no recognised suffix: follow the chosen filter
                path += ".json" if "json" in chosen.lower() else ".html"
            self.export_to(Path(path))

    def export_to(self, path: Path) -> bool:
        """Write the latest scan to path. Reports the outcome to the user; never raises."""
        store = Store(self.db_path)
        try:
            saved = export.export_latest(store, self.host_id, path)
        except (LookupError, ValueError, OSError) as exc:
            log.warning("report export failed", extra={"path": str(path), "err": str(exc)})
            self.dashboard.flash(f"Could not export: {exc}")
            return False
        finally:
            store.close()
        log.info("report exported", extra={"path": str(saved)})
        self.dashboard.flash(f"Saved {saved.name}")
        return True

    def _on_acknowledge(self, finding_id: str) -> None:
        """The user has seen a finding. Recorded in the database; evidence can still reopen it."""
        store = Store(self.db_path)
        try:
            store.acknowledge(finding_id)
            self.last_record = store.latest_scan(self.host_id) or self.last_record
        finally:
            store.close()
        log.info("finding acknowledged", extra={"finding_id": finding_id})
        self.dashboard.show_record(self.last_record)

    def _on_scan_started(self) -> None:
        log.info("scan started")
        self.dashboard.set_scanning(True)
        self.companion.request_state(State.SCANNING)

    def _on_scan_finished(self, record: ScanRecord) -> None:
        self.last_record = record
        self._export_action.setEnabled(True)
        self.dashboard.set_scanning(False)
        self.dashboard.show_record(record)
        self.companion.request_state(State.IDLE)  # leave SCANNING
        reaction = react(record)
        if reaction.state is not None:
            self.companion.request_state(reaction.state, reaction.tone)
        if self.config.notifications and not self.companion.machine.paused:
            for note in reaction.notifications:
                self._notify(note)
        log.info(
            "scan reaction",
            extra={
                "state": reaction.state.value if reaction.state else None,
                "tone": reaction.tone.value,
                "notifications": len(reaction.notifications),
            },
        )

    def _on_scan_failed(self, message: str) -> None:
        log.error("scan failed", extra={"error": message})
        self.dashboard.set_scanning(False)
        self.companion.request_state(State.IDLE)

    def _notify(self, note: Notification) -> None:
        if not self.tray_available:
            return
        self.tray.showMessage(note.title, note.body, _TRAY_ICON[note.severity], NOTIFICATION_MS)

    def _refresh_scan_action(self) -> None:
        if self.monitor.paused:
            self._scan_action.setText("Run scan")
            self._scan_action.setToolTip("Resume monitoring to run a scan")
        elif self.monitor.running:
            self._scan_action.setText("Scanning...")
            self._scan_action.setToolTip("A scan is in progress")
        else:
            self._scan_action.setText("Run scan")
            self._scan_action.setToolTip("Check this machine now (read-only)")
        self._scan_action.setEnabled(self.monitor.can_run)

    # -- lifecycle -----------------------------------------------------------
    def shutdown(self) -> None:
        """Release Qt objects deterministically while the QApplication still exists."""
        self.monitor.pause()
        QThreadPool.globalInstance().waitForDone(5000)
        self.tray.hide()
        for widget in (self.companion, self.dashboard, self.status_panel):
            widget.close()
            widget.deleteLater()
        QApplication.processEvents()
        clear_pixmap_caches()

    @property
    def tray_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _action(
        self,
        text: str,
        slot: Callable[..., object],
        checkable: bool = False,
        checked: bool = False,
    ) -> QAction:
        action = QAction(text, self.menu)
        action.setCheckable(checkable)
        action.setChecked(checked)  # before connecting: the slot must not fire during setup
        (action.toggled if checkable else action.triggered).connect(slot)
        return action

    def start(self) -> None:
        size = canvas_size(self.config.scale)
        screens = [s.availableGeometry() for s in QApplication.screens()]
        if self.config.companion_x is None or self.config.companion_y is None:
            pos = self.companion.default_position()
        else:
            pos = clamp_to_screens(
                QPoint(self.config.companion_x, self.config.companion_y), size, screens
            )
        self.companion.place(pos, settle=True)  # stand on the floor; no drop at startup
        self.companion.show()
        self.tray.show()
        if self.config.monitoring_paused:
            self._pause_action.setChecked(True)  # also puts the companion to sleep
        self.monitor.start()

    # -- slots ---------------------------------------------------------------
    def show_status(self) -> None:
        log.info("status panel opened")
        self.status_panel.update_status(
            self.companion.machine.state, self.companion.machine.paused, self.last_record
        )
        self.status_panel.show_near(
            self.companion.mapToGlobal(QPoint(self.companion.width() // 2, 0))
        )

    def show_dashboard(self) -> None:
        log.info("dashboard opened")
        self.dashboard.show_record(self.last_record)
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def _on_pause_toggled(self, paused: bool) -> None:
        self.companion.set_paused(paused)
        if paused:
            self.monitor.pause()
        else:
            self.monitor.resume()
        self.config.monitoring_paused = paused
        self.config.save()
        log.info("monitoring paused" if paused else "monitoring resumed")

    def _on_roam_toggled(self, roam: bool) -> None:
        self.companion.set_roam(roam)
        self.config.roam = roam
        self.config.save()
        log.info("roaming enabled" if roam else "roaming disabled")

    def _remember_position(self, pos: QPoint) -> None:
        self.config.companion_x, self.config.companion_y = pos.x(), pos.y()
        self.config.save()
        log.info("companion moved", extra={"x": pos.x(), "y": pos.y()})

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_dashboard()


def _single_instance_lock() -> QLockFile:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
    lock = QLockFile(str(runtime / "sentinel-ai.lock"))
    lock.setStaleLockTime(0)  # a crashed instance must not block the next start
    return lock


def scan_once(db_path: Path | None = None) -> int:
    """`sentinel-ai --scan`: one read-only assessment, printed, with no GUI."""
    reader = LocalReader()
    store = Store(db_path or default_db_path())
    try:
        record = run_scan(reader, store, host_identity(reader))
    finally:
        store.close()
    sys.stdout.write(render_text(record))
    return 0


def export_cli(args: list[str], db_path: Path | None = None) -> int:
    """`sentinel-ai --export FILE [--scan]`: write an audit evidence report, no GUI.

    Without --scan the most recent stored scan is exported; with it a fresh scan runs first.
    """
    try:
        target = Path(args[args.index("--export") + 1])
    except IndexError:
        sys.stderr.write("--export needs a file name ending in .html or .json\n")
        return 2
    if export.kind_for(target) is None:
        sys.stderr.write(f"cannot tell the format from {target.name!r}: use .html or .json\n")
        return 2
    reader = LocalReader()
    host = host_identity(reader)
    store = Store(db_path or default_db_path())
    try:
        if "--scan" in args:
            run_scan(reader, store, host)
        saved = export.export_latest(store, host, target)
    except LookupError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(f"could not write the report: {exc}\n")
        return 1
    finally:
        store.close()
    sys.stdout.write(f"Saved {saved}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if "--export" in args[1:]:
        configure_logging(level=logging.WARNING)
        return export_cli(args[1:])
    if "--scan" in args[1:]:
        configure_logging(level=logging.WARNING)  # keep the report readable on a terminal
        return scan_once()
    configure_logging()

    qapp = QApplication(args)
    qapp.setApplicationName("Sentinel AI")
    qapp.setQuitOnLastWindowClosed(False)  # closing the dashboard must not stop monitoring
    apply_app_font(qapp)
    qapp.setStyleSheet(build_stylesheet())

    lock = _single_instance_lock()
    if not lock.tryLock(100):
        log.error("another Sentinel AI instance is already running")
        return 1

    app = SentinelApp(qapp, Config.load())
    app.start()
    code = qapp.exec()
    app.shutdown()
    return code
