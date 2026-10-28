import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fakes import assessment, record
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.ui import format as fmt

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize(
    "delta, text",
    [
        ({"seconds": 5}, "just now"),
        ({"seconds": 44}, "just now"),
        ({"seconds": 90}, "2 min ago"),
        ({"minutes": 59}, "59 min ago"),
        ({"hours": 3}, "3 h ago"),
        ({"days": 1}, "1 day ago"),
        ({"days": 5}, "5 days ago"),
    ],
)
def test_relative_time(delta, text) -> None:
    assert fmt.relative(ago(**delta), NOW) == text


def test_local_clock_converts_from_utc_to_the_local_zone(monkeypatch) -> None:
    stamp = "2026-10-27T05:28:24Z"
    for zone, expected in (("UTC", "Oct 27, 05:28"), ("Etc/GMT+4", "Oct 27, 01:28")):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        assert fmt.local_clock(stamp) == expected
    monkeypatch.delenv("TZ")
    time.tzset()


def test_assessments_sort_worst_first_with_severity_breaking_ties() -> None:
    items = (
        assessment(Result.PASS, control_id="P"),
        assessment(Result.UNKNOWN, control_id="U"),
        assessment(Result.WARNING, Severity.LOW, "W-low"),
        assessment(Result.FAIL, Severity.MEDIUM, "F-med"),
        assessment(Result.ERROR, control_id="E"),
        assessment(Result.WARNING, Severity.HIGH, "W-high"),
        assessment(Result.FAIL, Severity.HIGH, "F-high"),
    )
    order = [a.control.control_id for a in fmt.sorted_assessments(record(items))]
    assert order == ["F-high", "F-med", "W-high", "W-low", "E", "U", "P"]


def head(*items):
    return fmt.headline(record(items))


def test_headline_leads_with_the_worst_state() -> None:
    f = assessment(Result.FAIL, Severity.HIGH, "FW-01")
    w = assessment(Result.WARNING, Severity.MEDIUM, "NET-01")
    p = assessment(Result.PASS, control_id="X")
    u = assessment(Result.UNKNOWN, control_id="U")
    assert fmt.headline(None)[1] == "No assessment yet"
    assert head(f, w, p) == (
        Result.FAIL,
        "1 control failing",
        "Most urgent: Control FW-01 and 1 other issue.",
    )
    assert head(w, p)[:2] == (Result.WARNING, "1 warning")
    assert head(f, replace(f, check_id="b"))[1] == "2 controls failing"
    assert head(u, p)[:2] == (Result.UNKNOWN, "Nothing failing, 1 control not assessed")
    assert head(p) == (
        Result.PASS,
        "All checks passing",
        "1 control checked; nothing needs attention.",
    )


def test_unassessed_controls_are_never_described_as_passing() -> None:
    _, title, sub = head(assessment(Result.UNKNOWN, control_id="U"))
    assert "passing" not in title.lower() and "not counted as passing" in sub


def test_text_is_escaped_so_scan_content_can_never_be_markup() -> None:
    nasty = '<img src=x onerror="alert(1)"> & "quoted"'
    out = fmt.escape(nasty)
    assert "<" not in out and ">" not in out and "&lt;img" in out and "&quot;" in out


def test_backtick_spans_become_code_and_everything_is_escaped() -> None:
    html = fmt.rich_commands("Run `sudo ufw enable` then <b>check</b>")
    assert "<b>sudo ufw enable</b>" in html  # the code span, our own markup
    assert "&lt;b&gt;check&lt;/b&gt;" in html  # the user text, neutralised
    assert "<script>" not in fmt.rich_commands("`<script>alert(1)</script>`")
