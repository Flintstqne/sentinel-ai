from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QLabel

from fakes import assessment, finding, record
from sentinel_ai.companion.state_machine import State
from sentinel_ai.monitoring.model import Evidence, ResolutionStatus, Result, Severity
from sentinel_ai.ui.dashboard import DashboardWindow
from sentinel_ai.ui.fonts import apply_app_font, load_fonts
from sentinel_ai.ui.icons import duck_head, status_pixmap
from sentinel_ai.ui.panels import NO_SCAN, StatusPanel
from sentinel_ai.ui.theme import build_stylesheet

F = assessment(Result.FAIL, Severity.HIGH, "FW-01")
W = assessment(Result.WARNING, Severity.MEDIUM, "NET-01")
P = assessment(Result.PASS, control_id="PATCH-01")
U = assessment(Result.UNKNOWN, control_id="AUTH-01")
FINDING = finding(Severity.HIGH, Result.FAIL, "FW-01")
REC = record(
    (P, U, W, F), open_findings=(FINDING, finding(Severity.MEDIUM, Result.WARNING, "NET-01"))
)


@pytest.fixture
def styled(qapp):
    """The app as it really runs: bundled fonts and the global stylesheet applied."""
    apply_app_font(qapp)
    qapp.setStyleSheet(build_stylesheet())
    yield qapp
    qapp.setStyleSheet("")


@pytest.fixture
def dash(styled):
    w = DashboardWindow()
    w.show()
    yield w
    w.close()


def test_the_bundled_fonts_are_present_and_load(qapp) -> None:
    families = load_fonts()
    assert {"IBM Plex Sans", "IBM Plex Mono", "Pixelify Sans"} <= families


def test_the_type_scale_survives_the_global_stylesheet(dash, qapp) -> None:
    """Regression: a font-size in the app stylesheet silently overrode every QFont set in code,
    so the big figures, the pixel wordmark and the mono evidence all rendered as 13px sans."""
    shown = replace(F, evidence=(Evidence("ufw.conf", "ENABLED=no"),))
    dash.show_record(record((shown,), open_findings=()))
    qapp.processEvents()

    def label(text: str) -> QLabel:
        found = [lb for lb in dash.findChildren(QLabel) if lb.text() == text]
        assert found, f"no label with text {text!r}"
        return found[0]

    figure = dash._tiles["pass"].value
    assert (figure.font().pixelSize(), figure.font().family()) == (30, "IBM Plex Sans")
    wordmark = label("SENTINEL")
    assert (wordmark.font().pixelSize(), wordmark.font().family()) == (18, "Pixelify Sans")
    evidence = label("ENABLED=no")
    assert (evidence.font().pixelSize(), evidence.font().family()) == (12, "IBM Plex Mono")


def test_a_new_dashboard_starts_empty_with_a_single_call_to_action(dash) -> None:
    assert dash._stack.currentIndex() == 0
    assert dash.row_titles() == []
    assert not dash._scan_button.isVisibleTo(dash)  # the empty state carries the one button
    spy = QSignalSpy(dash.scan_requested)
    QTest.mouseClick(dash._empty_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1


def test_results_are_listed_worst_first_and_the_worst_is_selected(dash) -> None:
    dash.show_record(REC)
    assert dash.row_check_ids() == ["FW-01", "NET-01", "AUTH-01", "PATCH-01"]
    assert dash.selected_check_id == "FW-01"
    assert dash._stack.currentIndex() == 1 and dash._scan_button.isVisibleTo(dash)


def test_tiles_show_exact_counts_from_the_assessments(dash) -> None:
    dash.show_record(REC)
    tiles = {k: t.value.text() for k, t in dash._tiles.items()}
    assert tiles == {"pass": "1", "warning": "1", "fail": "1", "unassessed": "1", "open": "2"}
    assert dash._tiles["open"].hint.text() == "1 high severity"
    assert dash._tiles["pass"].hint.text() == "of 4 controls"


def test_the_lead_statement_matches_the_state(dash) -> None:
    dash.show_record(REC)
    assert dash._lead_title.text() == "1 control failing"
    dash.show_record(record((P,), open_findings=()))
    assert dash._lead_title.text() == "All checks passing"


def test_selecting_a_control_shows_its_detail_and_the_arrow_keys_navigate(dash) -> None:
    dash.show_record(REC)
    assert "fail explanation" in dash.detail_text()
    QTest.keyClick(dash, Qt.Key.Key_Down)
    assert dash.selected_check_id == "NET-01" and "warning explanation" in dash.detail_text()
    QTest.keyClick(dash, Qt.Key.Key_Up)
    QTest.keyClick(dash, Qt.Key.Key_Up)  # already at the top: stays put
    assert dash.selected_check_id == "FW-01"


def test_selection_survives_a_refresh_with_new_results(dash) -> None:
    dash.show_record(REC)
    dash.select("NET-01")
    dash.show_record(REC)
    assert dash.selected_check_id == "NET-01"
    dash.show_record(record((P,), open_findings=()))  # that control is gone: fall back sensibly
    assert dash.selected_check_id == "PATCH-01"


def test_evidence_is_shown_as_plain_text_never_markup(dash) -> None:
    hostile = '<img src=x onerror="alert(1)"> bob'
    bad = assessment(Result.FAIL, Severity.HIGH, "AUTH-01")
    bad = type(bad)(
        bad.check_id,
        bad.control,
        bad.result,
        bad.severity,
        "<b>bold?</b> explanation",
        (Evidence("/var/log/auth.log", f"Invalid user {hostile}"),),
        "Run `<script>x</script>` now",
    )
    dash.show_record(record((bad,), open_findings=(finding(control_id="AUTH-01"),)))
    labels = dash._detail_scroll.widget().findChildren(QLabel)
    evidence = [lb for lb in labels if "Invalid user" in lb.text()]
    assert evidence and evidence[0].textFormat() == Qt.TextFormat.PlainText
    assert hostile in evidence[0].text()  # shown literally
    rich = [lb for lb in labels if lb.textFormat() == Qt.TextFormat.RichText]
    assert rich and all("<img" not in lb.text() and "<script>" not in lb.text() for lb in rich)
    assert any("&lt;b&gt;bold?" in lb.text() for lb in rich)


def test_recommended_commands_can_be_copied_but_are_never_run(dash) -> None:
    advice = replace(
        F, remediation="First `sudo ufw default deny incoming`, then `sudo ufw enable`."
    )
    dash.show_record(record((advice,), open_findings=(FINDING,)))
    buttons = dash._detail_scroll.widget().findChildren(type(dash._scan_button))
    copy = next(b for b in buttons if b.text() == "Copy commands")
    QTest.mouseClick(copy, Qt.MouseButton.LeftButton)
    assert QGuiApplication.clipboard().text() == "sudo ufw default deny incoming\nsudo ufw enable"
    assert copy.text() == "Copied"
    assert "Sentinel never runs these for you." in dash.detail_text()
    plain = replace(F, remediation="Do something sensible.")
    dash.show_record(record((plain,), open_findings=(FINDING,)))
    names = [b.text() for b in dash._detail_scroll.widget().findChildren(type(dash._scan_button))]
    assert "Copy commands" not in names  # no commands, no button


def test_only_open_findings_offer_acknowledge_and_it_emits_the_id(dash) -> None:
    dash.show_record(REC)
    ack = QSignalSpy(dash.acknowledge_requested)
    buttons = dash._detail_scroll.widget().findChildren(type(dash._scan_button))
    button = next(b for b in buttons if b.text() == "Acknowledge")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert ack.count() == 1 and ack.at(0)[0] == FINDING.finding_id
    done = finding(Severity.HIGH, Result.FAIL, "FW-01", ResolutionStatus.ACKNOWLEDGED)
    dash.show_record(record((F,), open_findings=(done,)))
    names = [b.text() for b in dash._detail_scroll.widget().findChildren(type(dash._scan_button))]
    assert "Acknowledge" not in names and "Acknowledged" in dash._rows[0].meta.text()


def test_scanning_dims_the_results_without_replacing_them(dash) -> None:
    dash.show_record(REC)
    dash.set_scanning(True)
    assert dash._dim.opacity() == pytest.approx(0.55)
    assert not dash._scan_button.isEnabled() and dash._scan_button.text() == "Scanning..."
    assert dash.row_titles()  # the previous results are still there
    assert "Scanning" in dash._when.text()
    dash.set_scanning(False)
    assert dash._dim.opacity() == 1.0 and dash._scan_button.isEnabled()
    fresh = DashboardWindow()
    fresh.set_scanning(True)
    assert fresh._dim.opacity() == 1.0 and not fresh._empty_button.isEnabled()  # nothing to dim


def test_every_status_has_its_own_icon_shape_not_just_its_own_colour(qapp) -> None:
    def shape(result: Result) -> frozenset:
        image = status_pixmap(result, 1).toImage()
        return frozenset(
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )

    shapes = {r: shape(r) for r in Result}
    assert len(set(shapes.values())) == len(Result), "two statuses share an icon shape"
    assert all(shapes.values())
    assert not duck_head(2).isNull()


def test_status_popup_shows_real_counts_or_says_there_is_no_scan(styled) -> None:
    panel = StatusPanel()
    panel.update_status(State.IDLE, False, None)
    assert panel._summary.text() == NO_SCAN
    panel.update_status(State.SLEEPING, True, REC)
    assert panel._state.text() == "Paused" and panel._monitoring.text() == "Paused"
    texts = [lb.text() for lb in panel.findChildren(QLabel)]
    assert {"passing", "warning", "failing"} <= set(texts)  # singular: there is one warning
    assert texts.count("1") >= 3  # one count each for passing, warnings and failing
    assert "Open findings: 2 (1 high)" in panel._summary.text()


def test_status_popup_can_open_the_dashboard(styled) -> None:
    panel = StatusPanel()
    spy = QSignalSpy(panel.open_dashboard_requested)
    panel._open.click()
    assert spy.count() == 1


def test_status_popup_never_clips_its_text_at_its_fixed_width(styled) -> None:
    """Regression: icon-plus-sentence count cells were wider than the popup and clipped
    ("4 passi", "1 failin"). Found only on a real desktop; offscreen layout hid it."""
    panel = StatusPanel()
    panel.update_status(State.IDLE, False, REC)
    panel.show()
    panel.adjustSize()
    styled.processEvents()
    for label in panel.findChildren(QLabel):
        if not label.text() or label.pixmap() is not None and not label.pixmap().isNull():
            continue
        needed = label.fontMetrics().horizontalAdvance(label.text())
        if not label.wordWrap():
            assert label.width() >= needed, (
                f"{label.text()!r} is clipped: {label.width()} < {needed}"
            )
    panel.close()


def test_export_button_appears_only_with_results_and_emits_a_request(dash) -> None:
    assert not dash._export_button.isVisibleTo(dash)
    dash.show_record(REC)
    assert dash._export_button.isVisibleTo(dash)
    spy = QSignalSpy(dash.export_requested)
    QTest.mouseClick(dash._export_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1


def test_flash_shows_a_confirmation_then_restores_the_normal_header(dash) -> None:
    dash.show_record(REC)
    normal = dash._when.text()
    dash.flash("Saved report.html", ms=40)
    assert dash._when.text() == "Saved report.html"
    QTest.qWait(150)
    assert dash._when.text() == normal and normal.startswith("Last scan")
