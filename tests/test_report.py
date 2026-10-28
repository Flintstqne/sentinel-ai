from fakes import assessment, finding, record
from sentinel_ai.monitoring.model import ResolutionStatus, Result, Severity
from sentinel_ai.monitoring.report import render_text


def test_report_shows_every_assessment_with_its_evidence_and_advice() -> None:
    rec = record(
        (assessment(Result.FAIL, Severity.HIGH, "FW-01"), assessment(Result.PASS, control_id="B")),
        new=(finding(),),
    )
    text = render_text(rec)
    assert "[FAIL] FW-01" in text and "[PASS] B" in text
    assert "evidence: src: obs" in text and "recommended: do something" in text
    assert "Read-only: nothing on this system was changed." in text
    assert "pass: 1  fail: 1" in text and "F-T-01-0001" in text


def test_report_with_no_findings_says_none_and_marks_acknowledged_ones() -> None:
    assert "  none" in render_text(record((assessment(Result.PASS),), open_findings=()))
    ack = finding(status=ResolutionStatus.ACKNOWLEDGED)
    assert "(acknowledged)" in render_text(record(open_findings=(ack,)))
