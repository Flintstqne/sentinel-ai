"""The dashboard: what was found, how bad it is, the evidence, and what to do about it.

Layout, top to bottom: a header (identity, last scan, Scan now), a one-sentence lead
statement, a row of stat tiles, then a master/detail pair: controls sorted worst-first on
the left, the selected control's explanation, evidence and recommended action on the right.

Rules it follows: status colour only ever fills a mark (an icon, a square), never text, and
every status has its own icon shape and a word; figures use the plain sans; scan text is
always escaped or shown as plain text (evidence can contain attacker-controlled strings);
while a scan runs the previous results stay on screen, dimmed, so nothing jumps.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sentinel_ai import __version__
from sentinel_ai.monitoring.model import Assessment, Finding, ResolutionStatus, Result, Severity
from sentinel_ai.monitoring.reaction import summarise
from sentinel_ai.monitoring.store import ScanRecord
from sentinel_ai.ui import format as fmt
from sentinel_ai.ui.fonts import load_fonts, make_font
from sentinel_ai.ui.icons import duck_full, duck_head, status_pixmap
from sentinel_ai.ui.theme import PALETTE, SPACE

NO_SCAN_TITLE = "No assessment yet"
_COMMAND = re.compile(r"`([^`]+)`")


def _label(
    text: str,
    role: str = "body",
    muted: bool = False,
    wrap: bool = False,
    rich: bool = False,
) -> QLabel:
    label = QLabel(text)
    label.setFont(make_font(role))
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    if muted:
        label.setStyleSheet(f"color: {PALETTE.text_muted};")
    return label


def _section_label(text: str) -> QLabel:
    label = _label(text.upper(), "label", muted=True)
    label.setContentsMargins(0, SPACE["lg"], 0, SPACE["xs"])
    return label


def _icon_label(result: Result, scale: int = 2) -> QLabel:
    label = QLabel()
    label.setPixmap(status_pixmap(result, scale))
    label.setFixedSize(9 * scale, 9 * scale)
    label.setAccessibleName(fmt.RESULT_LABEL[result])
    return label


def _rule() -> QFrame:
    rule = QFrame()
    rule.setObjectName("rule")
    rule.setFrameShape(QFrame.Shape.NoFrame)
    return rule


def _mark_colour(result: Result) -> str:
    return {
        Result.PASS: PALETTE.good,
        Result.WARNING: PALETTE.warning,
        Result.FAIL: PALETTE.danger,
    }.get(result, PALETTE.neutral)


class Chip(QFrame):
    """A small bordered fact: an optional coloured square mark, then ink-coloured text."""

    def __init__(self, text: str, mark: str | None = None) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {PALETTE.surface_raised}; border: 1px solid {PALETTE.border};"
            "border-radius: 2px; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)
        if mark:
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {mark}; border: none;")
            lay.addWidget(dot)
        self.text = _label(text, "small")
        self.text.setStyleSheet("border: none;")
        lay.addWidget(self.text)


class Tile(QFrame):
    """A stat tile: sentence-case label, the figure in plain sans, optional hint."""

    def __init__(self, label: str, icon: Result | None) -> None:
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        lay.setSpacing(SPACE["xs"])
        top = QHBoxLayout()
        top.addWidget(_label(label, "small", muted=True))
        top.addStretch(1)
        if icon is not None:
            top.addWidget(_icon_label(icon))
        lay.addLayout(top)
        self.value = _label("-", "figure")
        lay.addWidget(self.value)
        self.hint = _label("", "caption", muted=True)
        lay.addWidget(self.hint)

    def set(self, value: str, hint: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)


class ControlRow(QPushButton):
    """One control in the list: status icon, title, and a muted line of facts."""

    def __init__(self, assessment: Assessment, finding: Finding | None) -> None:
        super().__init__()
        self.check_id = assessment.check_id
        self.setObjectName("row")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        lay.setSpacing(SPACE["md"])
        icon = _icon_label(assessment.result)
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = _label(assessment.control.title, "body_strong", wrap=True)
        facts = [assessment.control.control_id, fmt.RESULT_LABEL[assessment.result]]
        if assessment.is_issue:
            facts.append(fmt.SEVERITY_LABEL[assessment.severity])
        if finding and finding.resolution_status is ResolutionStatus.ACKNOWLEDGED:
            facts.append("Acknowledged")
        self.meta = _label("  ·  ".join(facts), "small", muted=True)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        lay.addLayout(text, 1)
        for child in (icon, self.title, self.meta):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName(f"{assessment.control.title}, {fmt.RESULT_LABEL[assessment.result]}")


class DashboardWindow(QWidget):
    scan_requested = Signal()
    acknowledge_requested = Signal(str)  # finding_id
    export_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        load_fonts()
        self.setWindowTitle("Sentinel AI")
        self.setWindowIcon(QIcon(duck_head(4)))
        self.setMinimumSize(920, 620)
        self.resize(1080, 700)
        self._record: ScanRecord | None = None
        self._scanning = False
        self._selected: str | None = None
        self._rows: list[ControlRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_empty())
        self._stack.addWidget(self._build_body())
        root.addWidget(self._stack, 1)
        root.addWidget(self._build_footer())
        self._dim = QGraphicsOpacityEffect(self._stack)
        self._dim.setOpacity(1.0)
        self._stack.setGraphicsEffect(self._dim)
        self.show_record(None)

    # -- construction --------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(64)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(SPACE["xl"], 0, SPACE["xl"], 0)
        lay.setSpacing(SPACE["md"])
        logo = QLabel()
        logo.setPixmap(duck_head(3))
        lay.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addStretch(1)
        names.addWidget(_label("SENTINEL", "wordmark"))
        self._host = _label("", "mono", muted=True)
        self._host.setFont(make_font("caption"))
        names.addWidget(self._host)
        names.addStretch(1)
        lay.addLayout(names)
        lay.addStretch(1)
        self._when = _label("", "small", muted=True)
        self._when.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._when)
        self._export_button = QPushButton("Export report")
        self._export_button.setFont(make_font("body_strong"))
        self._export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_button.setToolTip("Save an audit evidence report (HTML or JSON)")
        self._export_button.clicked.connect(self.export_requested)
        lay.addWidget(self._export_button)
        self._scan_button = QPushButton("Scan now")
        self._scan_button.setObjectName("primary")
        self._scan_button.setFont(make_font("body_strong"))
        self._scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_button.clicked.connect(self.scan_requested)
        lay.addWidget(self._scan_button)
        return header

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("footer")
        footer.setFixedHeight(36)
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(SPACE["xl"], 0, SPACE["xl"], 0)
        lay.addWidget(
            _label("Read-only: Sentinel never changes anything on this system.", "caption", True)
        )
        lay.addStretch(1)
        lay.addWidget(_label(f"v{__version__}", "caption", True))
        return footer

    def _build_empty(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(SPACE["md"])
        art = QLabel()
        art.setPixmap(duck_full(6))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(art)
        title = _label(NO_SCAN_TITLE, "headline")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        self._empty_text = _label(
            "Run a scan to see how this machine measures up.", "body", muted=True
        )
        self._empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._empty_text)
        self._empty_button = QPushButton("Run first scan")
        self._empty_button.setObjectName("primary")
        self._empty_button.setFont(make_font("body_strong"))
        self._empty_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._empty_button.clicked.connect(self.scan_requested)
        lay.addWidget(self._empty_button, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_body(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["lg"])
        lay.setSpacing(SPACE["xl"] - SPACE["xs"])

        lead = QHBoxLayout()
        lead.setSpacing(SPACE["md"])
        self._lead_icon = QLabel()
        lead.addWidget(self._lead_icon, 0, Qt.AlignmentFlag.AlignTop)
        lead_text = QVBoxLayout()
        lead_text.setSpacing(2)
        self._lead_title = _label("", "headline")
        self._lead_sub = _label("", "body", muted=True, wrap=True)
        lead_text.addWidget(self._lead_title)
        lead_text.addWidget(self._lead_sub)
        lead.addLayout(lead_text, 1)
        lay.addLayout(lead)

        tiles = QHBoxLayout()
        tiles.setSpacing(SPACE["md"])
        self._tiles = {
            "pass": Tile("Passing", Result.PASS),
            "warning": Tile("Warnings", Result.WARNING),
            "fail": Tile("Failing", Result.FAIL),
            "unassessed": Tile("Not assessed", Result.UNKNOWN),
            "open": Tile("Open findings", None),
        }
        for tile in self._tiles.values():
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tiles.addWidget(tile)
        lay.addLayout(tiles)

        split = QHBoxLayout()
        split.setSpacing(SPACE["lg"])
        left = QVBoxLayout()
        left.setSpacing(0)
        header = _label("CONTROLS", "label", muted=True)
        header.setContentsMargins(SPACE["lg"], 0, 0, SPACE["sm"])
        left.addWidget(header)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        scroll.setFixedWidth(360)
        left.addWidget(scroll, 1)
        split.addLayout(left)

        self._detail_card = QFrame()
        self._detail_card.setObjectName("card")
        card_lay = QVBoxLayout(self._detail_card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        card_lay.addWidget(self._detail_scroll)
        split.addWidget(self._detail_card, 1)
        lay.addLayout(split, 1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        return body

    # -- public API ----------------------------------------------------------
    def show_record(self, record: ScanRecord | None) -> None:
        self._record = record
        self._refresh_header()
        if record is None:
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        self._fill_lead(record)
        self._fill_tiles(record)
        self._fill_list(record)

    def set_scanning(self, scanning: bool) -> None:
        """While scanning, keep what is on screen but dim it: no layout jump, no skeleton."""
        self._scanning = scanning
        self._dim.setOpacity(0.55 if scanning and self._record is not None else 1.0)
        self._refresh_header()
        self._empty_button.setEnabled(not scanning)
        self._empty_button.setText("Scanning..." if scanning else "Run first scan")

    def flash(self, text: str, ms: int = 6000) -> None:
        """Show a short confirmation in the header, then put the normal text back."""
        self._when.setText(text)
        QTimer.singleShot(ms, self._refresh_header)

    def select(self, check_id: str) -> None:
        for row in self._rows:
            if row.check_id == check_id:
                row.setChecked(True)
                self._show_detail(check_id)
                return

    @property
    def selected_check_id(self) -> str | None:
        return self._selected

    def row_titles(self) -> list[str]:
        return [r.title.text() for r in self._rows]

    def row_check_ids(self) -> list[str]:
        return [r.check_id for r in self._rows]

    def detail_text(self) -> str:
        """All text currently shown in the detail pane, for tests and screen readers."""
        widget = self._detail_scroll.widget()
        if widget is None:
            return ""
        return "\n".join(label.text() for label in widget.findChildren(QLabel) if label.text())

    # -- filling -------------------------------------------------------------
    def _refresh_header(self) -> None:
        record = self._record
        self._host.setText(record.host_id if record else "")
        if self._scanning:
            self._when.setText("Scanning...")
        elif record:
            stamp = record.finished_at
            self._when.setText(f"Last scan {fmt.relative(stamp)}  ·  {fmt.local_clock(stamp)}")
        else:
            self._when.setText("")
        self._scan_button.setEnabled(not self._scanning)
        self._scan_button.setText("Scanning..." if self._scanning else "Scan now")
        # With nothing scanned yet the empty state carries the one call to action.
        self._scan_button.setVisible(record is not None)
        self._export_button.setVisible(record is not None)

    def _fill_lead(self, record: ScanRecord) -> None:
        result, title, sub = fmt.headline(record)
        if result is not None:
            self._lead_icon.setPixmap(status_pixmap(result, 3))
        self._lead_title.setText(title)
        self._lead_sub.setText(sub)

    def _fill_tiles(self, record: ScanRecord) -> None:
        counts = summarise(record.assessments)
        total = len(record.assessments)
        self._tiles["pass"].set(str(counts["pass"]), f"of {total} controls")
        self._tiles["warning"].set(str(counts["warning"]))
        self._tiles["fail"].set(str(counts["fail"]))
        unassessed = counts["unknown"] + counts["error"]
        self._tiles["unassessed"].set(str(unassessed), "evidence unavailable" if unassessed else "")
        high = sum(1 for f in record.open_findings if f.severity >= Severity.HIGH)
        self._tiles["open"].set(
            str(len(record.open_findings)), f"{high} high severity" if high else ""
        )

    def _fill_list(self, record: ScanRecord) -> None:
        for row in self._rows:
            self._group.removeButton(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        by_control = {f.control_id: f for f in record.open_findings}
        for assessment in fmt.sorted_assessments(record):
            row = ControlRow(assessment, by_control.get(assessment.control.control_id))
            self._group.addButton(row)
            row.clicked.connect(lambda _=False, cid=assessment.check_id: self._on_row(cid))
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)
            self._rows.append(row)
        keep = self._selected if self._selected in self.row_check_ids() else None
        target = keep or (self._rows[0].check_id if self._rows else None)
        if target:
            self.select(target)

    def _on_row(self, check_id: str) -> None:
        self._show_detail(check_id)

    # -- detail --------------------------------------------------------------
    def _show_detail(self, check_id: str) -> None:
        self._selected = check_id
        if self._record is None:
            return
        assessment = next((a for a in self._record.assessments if a.check_id == check_id), None)
        if assessment is None:
            return
        finding = next(
            (
                f
                for f in self._record.open_findings
                if f.control_id == assessment.control.control_id
            ),
            None,
        )
        self._detail_scroll.setWidget(self._build_detail(assessment, finding))

    def _build_detail(self, a: Assessment, finding: Finding | None) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {PALETTE.surface};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(SPACE["md"])
        head.addWidget(_icon_label(a.result, 3), 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(_label(a.control.title, "title", wrap=True), 1)
        lay.addLayout(head)

        chips = QHBoxLayout()
        chips.setSpacing(SPACE["sm"])
        chips.setContentsMargins(0, SPACE["md"], 0, 0)
        chips.addWidget(Chip(fmt.RESULT_LABEL[a.result], _mark_colour(a.result)))
        if a.is_issue:
            chips.addWidget(Chip(f"{fmt.SEVERITY_LABEL[a.severity]} severity"))
        chips.addWidget(Chip(a.control.control_id))
        chips.addStretch(1)
        lay.addLayout(chips)

        lay.addWidget(_section_label("What we found"))
        explanation = _label(
            f'<p style="line-height:150%; margin:0;">{fmt.escape(a.explanation)}</p>',
            "body",
            wrap=True,
            rich=True,
        )
        lay.addWidget(explanation)

        if a.evidence:
            lay.addWidget(_section_label("Evidence"))
            lay.addWidget(self._evidence_block(a))

        if a.remediation:
            lay.addWidget(_section_label("Recommended action"))
            action = _label(
                f'<p style="line-height:150%; margin:0;">{fmt.rich_commands(a.remediation)}</p>',
                "body",
                wrap=True,
                rich=True,
            )
            lay.addWidget(action)
            commands = _COMMAND.findall(a.remediation)
            if commands:
                copy = QPushButton("Copy commands")
                copy.setObjectName("link")
                copy.setFont(make_font("small"))
                copy.setCursor(Qt.CursorShape.PointingHandCursor)
                copy.clicked.connect(lambda: self._copy("\n".join(commands), copy))
                lay.addSpacing(SPACE["sm"])
                lay.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(_label("Sentinel never runs these for you.", "caption", muted=True))

        if finding is not None:
            lay.addWidget(_section_label("Finding"))
            lay.addWidget(self._finding_block(finding))

        lay.addWidget(_section_label("Reference"))
        lay.addWidget(_label(a.control.reference, "small", muted=True, wrap=True))
        lay.addStretch(1)
        return page

    def _evidence_block(self, a: Assessment) -> QWidget:
        block = QFrame()
        block.setStyleSheet(
            f"QFrame {{ background: {PALETTE.bg}; border: 1px solid {PALETTE.border};"
            "border-radius: 2px; }"
        )
        lay = QVBoxLayout(block)
        lay.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        lay.setSpacing(SPACE["md"])
        for item in a.evidence:
            entry = QVBoxLayout()
            entry.setSpacing(1)
            source = _label(item.source, "mono", muted=True, wrap=True)
            small_mono = make_font("mono")
            small_mono.setPixelSize(11)
            source.setFont(small_mono)
            source.setStyleSheet(f"color: {PALETTE.text_muted}; border: none;")
            observation = _label(item.observation, "mono", wrap=True)  # plain text: never markup
            observation.setStyleSheet("border: none;")
            entry.addWidget(source)
            entry.addWidget(observation)
            lay.addLayout(entry)
        return block

    def _finding_block(self, f: Finding) -> QWidget:
        block = QWidget()
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["xs"])
        status = f.resolution_status.value.title()
        lay.addWidget(_label(f"{f.finding_id}  ·  {status}", "body_strong"))
        lay.addWidget(
            _label(
                f"First seen {fmt.local_clock(f.detected_at)}  ·  "
                f"last seen {fmt.relative(f.last_seen_at)}",
                "small",
                muted=True,
            )
        )
        if f.resolution_status is ResolutionStatus.OPEN:
            button = QPushButton("Acknowledge")
            button.setFont(make_font("body"))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip("Mark as seen. It stays on record and reopens if it gets worse.")
            button.clicked.connect(lambda: self.acknowledge_requested.emit(f.finding_id))
            lay.addSpacing(SPACE["sm"])
            lay.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        return block

    @staticmethod
    def _copy(text: str, button: QPushButton) -> None:
        QGuiApplication.clipboard().setText(text)
        button.setText("Copied")

    # -- keyboard ------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._rows:
            ids = self.row_check_ids()
            index = ids.index(self._selected) if self._selected in ids else 0
            index += 1 if event.key() == Qt.Key.Key_Down else -1
            self.select(ids[max(0, min(len(ids) - 1, index))])
            return
        super().keyPressEvent(event)
