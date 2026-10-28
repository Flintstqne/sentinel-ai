from fakes import assessment, finding, record
from sentinel_ai.companion.state_machine import State, Tone
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.monitoring.reaction import MAX_NOTIFICATIONS, react, summarise

PASS = assessment(Result.PASS)


def test_a_new_confirmed_high_severity_failure_goes_red() -> None:
    f = finding(Severity.HIGH, Result.FAIL)
    r = react(record((assessment(Result.FAIL, Severity.HIGH),), new=(f,)))
    assert (r.state, r.tone) == (State.ALERT, Tone.CRITICAL)
    assert len(r.notifications) == 1 and r.notifications[0].severity is Severity.HIGH


def test_a_high_severity_warning_is_not_confirmed_so_not_red() -> None:
    f = finding(Severity.HIGH, Result.WARNING)
    assert react(record(new=(f,))).tone is Tone.WARNING


def test_medium_issues_alert_amber_and_low_ones_only_give_an_insight() -> None:
    medium = react(record(new=(finding(Severity.MEDIUM, Result.WARNING),)))
    assert (medium.state, medium.tone) == (State.ALERT, Tone.WARNING)
    low = react(record(new=(finding(Severity.LOW, Result.WARNING),)))
    assert (low.state, low.tone) == (State.INSIGHT, Tone.NORMAL)


def test_escalation_counts_as_a_change() -> None:
    r = react(record(escalated=(finding(Severity.HIGH, Result.FAIL),)))
    assert (r.state, r.tone) == (State.ALERT, Tone.CRITICAL) and r.notifications


def test_unchanged_open_findings_do_not_re_alarm_every_scan() -> None:
    f = finding(Severity.HIGH, Result.FAIL)
    r = react(record((assessment(Result.FAIL, Severity.HIGH),), open_findings=(f,)))
    assert r.state is None and r.notifications == ()


def test_a_clean_scan_is_happy() -> None:
    r = react(record((PASS, assessment(Result.PASS, control_id="B")), open_findings=()))
    assert r.state is State.HAPPY and r.notifications == ()


def test_unassessable_checks_alone_neither_celebrate_nor_alarm() -> None:
    r = react(record((assessment(Result.UNKNOWN), assessment(Result.ERROR)), open_findings=()))
    assert r.state is None


def test_a_scan_with_some_passes_but_other_findings_open_is_not_happy() -> None:
    r = react(record((PASS,), open_findings=(finding(),)))
    assert r.state is None


def test_notifications_are_capped_ordered_and_summarised() -> None:
    new = tuple(finding(Severity.MEDIUM, Result.WARNING, f"C-{i}") for i in range(5))
    new += (finding(Severity.HIGH, Result.FAIL, "TOP"),)
    r = react(record(new=new))
    assert len(r.notifications) == MAX_NOTIFICATIONS + 1
    assert r.notifications[0].severity is Severity.HIGH
    assert "3 more" in r.notifications[-1].body


def test_notification_uses_the_control_title_and_the_explanation() -> None:
    a = assessment(Result.FAIL, Severity.HIGH, "FW-01")
    f = finding(Severity.HIGH, Result.FAIL, "FW-01")
    (n,) = react(record((a,), new=(f,))).notifications
    assert n.title == "High: Control FW-01" and n.body == "FW-01 explanation"


def test_summarise_counts_straight_from_the_assessments() -> None:
    counts = summarise(
        (PASS, assessment(Result.FAIL), assessment(Result.FAIL), assessment(Result.UNKNOWN))
    )
    assert counts == {"pass": 1, "fail": 2, "warning": 0, "unknown": 1, "error": 0}
