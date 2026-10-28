import pytest

from fakes import assessment
from sentinel_ai.monitoring.model import Result, Severity


def test_a_pass_cannot_exist_without_evidence() -> None:
    with pytest.raises(ValueError, match="evidence"):
        assessment(Result.PASS, evidence=False)


@pytest.mark.parametrize("result", [Result.FAIL, Result.WARNING, Result.UNKNOWN, Result.ERROR])
def test_other_results_may_be_recorded_without_evidence(result) -> None:
    assert assessment(result, evidence=False).result is result


def test_only_fail_and_warning_are_issues() -> None:
    issues = {r for r in Result if assessment(r).is_issue}
    assert issues == {Result.FAIL, Result.WARNING}


def test_severity_is_ordered() -> None:
    assert Severity.INFO < Severity.LOW < Severity.MEDIUM < Severity.HIGH < Severity.CRITICAL
