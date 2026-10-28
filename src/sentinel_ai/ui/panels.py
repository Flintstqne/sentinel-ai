"""The compact status popup (single click on the duck).

It shows only what the last scan actually found. With no scan yet it says so; it never fills
the space with example data. The full picture lives in the dashboard.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from sentinel_ai.companion.state_machine import State
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.monitoring.reaction import summarise
from sentinel_ai.monitoring.store import ScanRecord
from sentinel_ai.ui import format as fmt
from sentinel_ai.ui.dashboard import _icon_label, _label, _rule
from sentinel_ai.ui.fonts import load_fonts, make_font
from sentinel_ai.ui.icons import duck_head
from sentinel_ai.ui.theme import SPACE

NO_SCAN = "No assessment has run yet."

_STATE_TEXT = {
    State.IDLE: "Idle",
    State.SCANNING: "Scanning",
    State.ALERT: "Alert",
    State.THINKING: "Analysing",
    State.INSIGHT: "New insight",
    State.HAPPY: "Check complete",
    State.SLEEPING: "Paused",
}


def status_lines(record: ScanRecord | None) -> list[str]:
    """What the last scan found, as plain lines. Every number comes from the assessments."""
    if record is None:
        return [NO_SCAN]
    counts = summarise(record.assessments)
    lines = [
        f"Last scan: {fmt.relative(record.finished_at)}",
        f"Passing {counts[Result.PASS.value]}, warnings {counts[Result.WARNING.value]}, "
        f"failing {counts[Result.FAIL.value]}",
    ]
    unassessed = counts[Result.UNKNOWN.value] + counts[Result.ERROR.value]
    if unassessed:
        lines.append(f"{unassessed} check(s) could not be assessed")
    high = sum(1 for f in record.open_findings if f.severity >= Severity.HIGH)
    lines.append(
        f"Open findings: {len(record.open_findings)}" + (f" ({high} high)" if high else "")
    )
    return lines


class StatusPanel(QFrame):
    """Single-click popup: companion state, monitoring status and the last scan at a glance."""

    open_dashboard_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        load_fonts()
        self.setObjectName("panel")
        self.setFixedWidth(300)
        root = QVBoxLayout(self)
        m = SPACE["lg"]
        root.setContentsMargins(m, m, m, SPACE["md"])
        root.setSpacing(SPACE["md"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["sm"])
        logo = QLabel()
        logo.setPixmap(duck_head(2))
        head.addWidget(logo)
        head.addWidget(_label("SENTINEL", "wordmark"))
        head.addStretch(1)
        root.addLayout(head)
        root.addWidget(_rule())

        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE["lg"])
        grid.setVerticalSpacing(SPACE["xs"])
        self._state = _label("", "body")
        self._monitoring = _label("", "body")
        for row, (name, value) in enumerate(
            (("State", self._state), ("Monitoring", self._monitoring))
        ):
            grid.addWidget(_label(name, "small", muted=True), row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self._counts = QHBoxLayout()
        self._counts.setSpacing(SPACE["md"])
        root.addLayout(self._counts)
        self._summary = _label("", "small", muted=True, wrap=True)
        root.addWidget(self._summary)

        self._open = QPushButton("Open dashboard")
        self._open.setObjectName("link")
        self._open.setFont(make_font("body_strong"))
        self._open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open.clicked.connect(self._on_open)
        root.addWidget(self._open, 0, Qt.AlignmentFlag.AlignLeft)

    def update_status(self, state: State, paused: bool, record: ScanRecord | None = None) -> None:
        self._state.setText(_STATE_TEXT[state])
        self._monitoring.setText("Paused" if paused else "Active")
        while self._counts.count():
            item = self._counts.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if record is None:
            self._summary.setText(NO_SCAN)
            return
        counts = summarise(record.assessments)
        for result, key, word in (
            (Result.PASS, "pass", "passing"),
            (Result.WARNING, "warning", "warnings"),
            (Result.FAIL, "fail", "failing"),
        ):
            # A count over a caption: three of these fit the popup width with room to spare,
            # where icon-plus-sentence cells clipped ("4 passi") when measured on a real desktop.
            top = QHBoxLayout()
            top.setSpacing(SPACE["xs"])
            top.addWidget(_icon_label(result))
            top.addWidget(_label(str(counts[key]), "title"))
            top.addStretch(1)
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addLayout(top)
            noun = word[:-1] if word == "warnings" and counts[key] == 1 else word
            cell.addWidget(_label(noun, "small", muted=True))
            holder = QFrame()
            holder.setLayout(cell)
            holder.setStyleSheet("background: transparent;")
            self._counts.addWidget(holder, 1)
        lines = status_lines(record)
        self._summary.setText("  ·  ".join([lines[0], *lines[2:]]))

    def _on_open(self) -> None:
        self.hide()
        self.open_dashboard_requested.emit()

    def show_near(self, anchor: QPoint, panel_below: bool = False) -> None:
        self.adjustSize()
        y = anchor.y() + 8 if panel_below else anchor.y() - self.height() - 8
        self.move(max(0, anchor.x() - self.width() // 2), max(0, y))
        self.show()
