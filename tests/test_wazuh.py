import json
from datetime import timedelta

import pytest

from fakes import NOW, FakeReader
from sentinel_ai.monitoring.checks import wazuh
from sentinel_ai.monitoring.model import Result, Severity

F = wazuh.ALERTS_FILE


def alert(
    level=3, desc="PAM: Login session opened.", age=timedelta(hours=1), rule_id="5501", groups=None
):
    stamp = (NOW - age).strftime("%Y-%m-%dT%H:%M:%S.000%z")
    return json.dumps(
        {
            "timestamp": stamp,
            "rule": {
                "level": level,
                "description": desc,
                "id": rule_id,
                "groups": groups or ["pam"],
            },
            "agent": {"id": "000", "name": "mint"},
        }
    )


def host(*lines, mtime_age=timedelta(minutes=5), **kw) -> FakeReader:
    files = {wazuh.OSSEC_DIR: "", F: "\n".join(lines) + "\n"}
    return FakeReader(files, mtimes={F: NOW - mtime_age}, **kw)


def test_not_installed_is_unknown_never_pass() -> None:
    a = wazuh.check(FakeReader())
    assert a.result is Result.UNKNOWN and "not installed" in a.explanation


def test_installed_without_alerts_file_is_unknown() -> None:
    a = wazuh.check(FakeReader({wazuh.OSSEC_DIR: ""}))
    assert a.result is Result.UNKNOWN and "no alerts file" in a.explanation


def test_unreadable_alerts_file_is_unknown_and_names_the_group() -> None:
    a = wazuh.check(FakeReader({wazuh.OSSEC_DIR: ""}, unreadable={F}))
    assert a.result is Result.UNKNOWN and "'wazuh' group" in a.explanation


def test_stale_feed_is_unknown_even_with_no_alerts() -> None:
    a = wazuh.check(host(alert(), mtime_age=timedelta(hours=25)))
    assert a.result is Result.UNKNOWN and "not been written" in a.explanation


def test_missing_mtime_is_unknown() -> None:
    reader = host(alert())
    reader.mtimes.clear()
    assert wazuh.check(reader).result is Result.UNKNOWN


def test_fresh_empty_feed_passes_with_freshness_as_evidence() -> None:
    a = wazuh.check(host())
    assert a.result is Result.PASS
    assert any("last written" in e.observation for e in a.evidence)


def test_low_level_alerts_alone_pass() -> None:
    a = wazuh.check(host(alert(3), alert(6)))
    assert a.result is Result.PASS
    assert any(
        "2 security events in the last 24 hours, 0 at level 7" in e.observation for e in a.evidence
    )


@pytest.mark.parametrize(
    ("level", "result", "severity"),
    [
        (6, Result.PASS, Severity.INFO),
        (7, Result.WARNING, Severity.LOW),
        (9, Result.WARNING, Severity.LOW),
        (10, Result.WARNING, Severity.MEDIUM),
        (11, Result.WARNING, Severity.MEDIUM),
        (12, Result.FAIL, Severity.HIGH),
        (14, Result.FAIL, Severity.HIGH),
        (15, Result.FAIL, Severity.CRITICAL),
    ],
)
def test_level_boundaries(level, result, severity) -> None:
    a = wazuh.check(host(alert(level)))
    assert (a.result, a.severity) == (result, severity)


def test_highest_level_decides_and_each_flagged_rule_is_evidence() -> None:
    a = wazuh.check(host(alert(8, rule_id="100"), alert(13, rule_id="200"), alert(2)))
    assert (a.result, a.severity) == (Result.FAIL, Severity.HIGH)
    sources = [e.source for e in a.evidence]
    assert f"{F} rule 200" in sources and f"{F} rule 100" in sources
    assert sources.index(f"{F} rule 200") < sources.index(f"{F} rule 100")  # worst first


def test_alerts_older_than_the_window_are_ignored() -> None:
    a = wazuh.check(host(alert(13, age=timedelta(hours=25))))
    assert a.result is Result.PASS


def test_malformed_lines_are_counted_not_fatal() -> None:
    bad = [
        "{not json",
        '{"rule": "x"}',
        "[]",
        '{"rule": {"level": 9}, "timestamp": "nope"}',
        '{"rule": {"level": 9}, "timestamp": "2026-10-27T10:00:00"}',  # no zone: untrusted
        json.dumps(
            {"rule": {"level": 9}, "timestamp": "2026-10-27T11:00:00+00:00", "agent": "str"}
        ),
    ]
    a = wazuh.check(host(*bad, alert(3)))
    assert a.result is Result.PASS
    assert any(f"{len(bad)} lines could not be parsed" in e.observation for e in a.evidence)


def test_huge_and_hostile_descriptions_are_truncated_and_kept_as_text() -> None:
    hostile = "<img src=x onerror=alert(1)>\x1b[31m" + "A" * 10_000
    a = wazuh.check(host(alert(10, desc=hostile)))
    sample = next(e for e in a.evidence if "rule" in e.source)
    assert len(sample.observation) < 400
    assert "\x1b" not in sample.observation
    assert "<img" in sample.observation  # kept verbatim as text; the UI and export escape it


def test_samples_are_capped_per_rule() -> None:
    a = wazuh.check(host(*[alert(9, rule_id=str(i)) for i in range(20)]))
    assert sum("rule" in e.source for e in a.evidence) == wazuh.MAX_SAMPLES
    assert "20 security events" in a.explanation and "from 20 rules" in a.explanation


def test_repeats_of_one_rule_are_one_evidence_line_with_a_count_and_the_latest_example() -> None:
    lines = [alert(7, f"port {i}", age=timedelta(hours=5 - i), rule_id="533") for i in range(4)]
    a = wazuh.check(host(*lines, alert(8, rule_id="100")))
    rule_lines = [e for e in a.evidence if "rule" in e.source]
    assert [e.source.rsplit(" ", 1)[1] for e in rule_lines] == ["100", "533"]  # worst first
    assert rule_lines[1].observation.startswith("level 7, x4: port 3")  # latest example
    assert "5 security events" in a.explanation and "from 2 rules" in a.explanation


def test_rule_level_is_the_highest_seen_for_that_rule() -> None:
    a = wazuh.check(host(alert(7, rule_id="9"), alert(12, rule_id="9", age=timedelta(hours=3))))
    assert a.result is Result.FAIL
    assert next(e for e in a.evidence if "rule 9" in e.source).observation.startswith(
        "level 12, x2"
    )


def test_configuration_assessment_results_are_counted_but_not_judged() -> None:
    sca = [alert(7, "CIS: Ensure cramfs is disabled", rule_id="19007", groups=["sca"])] * 30
    a = wazuh.check(host(*sca, alert(3)))
    assert a.result is Result.PASS
    assert any("30 configuration-assessment (SCA) results" in e.observation for e in a.evidence)
    assert not any("rule 19007" in e.source for e in a.evidence)


def test_sca_exclusion_does_not_hide_real_events() -> None:
    a = wazuh.check(host(alert(10, rule_id="19007", groups=["sca"]), alert(12, rule_id="5712")))
    assert a.result is Result.FAIL and "1 security event at" in a.explanation


def test_odd_groups_field_is_not_treated_as_sca() -> None:
    line = json.dumps(
        {
            "timestamp": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000%z"),
            "rule": {"level": 9, "id": "1", "description": "d", "groups": "sca"},
        }
    )
    assert wazuh.check(host(line)).result is Result.WARNING
