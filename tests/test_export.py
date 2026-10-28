import json
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from fakes import assessment, finding, record
from sentinel_ai.monitoring import export
from sentinel_ai.monitoring.model import Evidence, ResolutionStatus, Result, Severity
from sentinel_ai.monitoring.summary import headline

WHEN = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
HOSTILE = '<img src=x onerror="alert(1)"> <script>steal()</script> & "q"'

F = assessment(Result.FAIL, Severity.HIGH, "FW-01")
W = assessment(Result.WARNING, Severity.MEDIUM, "NET-01")
P = assessment(Result.PASS, control_id="AUTH-01")
U = assessment(Result.UNKNOWN, control_id="PATCH-01")
OPEN = finding(Severity.HIGH, Result.FAIL, "FW-01")
DONE = replace(
    finding(Severity.MEDIUM, Result.WARNING, "NET-01"),
    finding_id="F-NET-01-0000",
    resolution_status=ResolutionStatus.RESOLVED,
    resolved_at="2026-10-27T02:00:00Z",
)
REC = record((W, F, U, P), open_findings=(OPEN,))


def build(rec=REC, findings=(OPEN, DONE), **kw):
    return export.build_report(rec, findings, generated_at=WHEN, privileged=False, **kw)


def test_every_control_is_reported_passes_included_in_a_stable_order() -> None:
    r = build()
    assert [c["control_id"] for c in r["controls"]] == ["AUTH-01", "FW-01", "NET-01", "PATCH-01"]
    by_id = {c["control_id"]: c for c in r["controls"]}
    assert by_id["AUTH-01"]["result"] == "pass" and by_id["AUTH-01"]["evidence"]
    assert by_id["PATCH-01"]["result"] == "unknown"  # not assessed is reported, not omitted
    assert by_id["FW-01"]["evidence"] == [{"source": "src", "observation": "obs"}]


def test_summary_counts_and_headline_come_straight_from_the_assessments() -> None:
    r = build()
    assert r["summary"]["counts"] == {"pass": 1, "fail": 1, "warning": 1, "unknown": 1, "error": 0}
    assert r["summary"]["controls_total"] == 4 and r["summary"]["open_findings"] == 1
    _, title, detail = headline(REC)
    assert (r["summary"]["headline"], r["summary"]["detail"]) == (title, detail)


def test_findings_include_resolved_history_and_controls_link_only_open_ones() -> None:
    r = build()
    assert {f["finding_id"]: f["status"] for f in r["findings"]} == {
        "F-FW-01-0001": "open",
        "F-NET-01-0000": "resolved",
    }
    by_id = {c["control_id"]: c for c in r["controls"]}
    assert by_id["FW-01"]["finding_id"] == "F-FW-01-0001"
    assert by_id["NET-01"]["finding_id"] is None  # its finding is resolved


def test_the_scan_block_records_that_collection_was_read_only() -> None:
    r = build()
    assert r["scan"]["read_only"] is True and r["scan"]["privileged"] is False
    assert export.build_report(REC, (), generated_at=WHEN, privileged=True)["scan"]["privileged"]
    assert any("deterministic" in item for item in r["limitations"])
    assert any("not a digital signature" in item for item in r["limitations"])


def test_the_checksum_verifies_detects_tampering_and_ignores_the_export_time() -> None:
    r = build()
    assert export.verify_integrity(r)
    later = export.build_report(
        REC, (OPEN, DONE), generated_at=datetime(2030, 1, 1, tzinfo=UTC), privileged=False
    )
    assert later["integrity"]["content_sha256"] == r["integrity"]["content_sha256"]
    tampered = json.loads(export.render_json(r))
    tampered["controls"][0]["result"] = "fail"
    assert not export.verify_integrity(tampered)
    tampered = json.loads(export.render_json(r))
    tampered["controls"][1]["evidence"][0]["observation"] = "edited"
    assert not export.verify_integrity(tampered)


def test_a_different_scan_has_a_different_checksum() -> None:
    other = record((W, replace(F, explanation="changed"), U, P), open_findings=(OPEN,))
    assert build(other)["integrity"]["content_sha256"] != build()["integrity"]["content_sha256"]


def test_json_round_trips_exactly_and_keeps_unicode() -> None:
    nasty = replace(F, explanation="café ✔ “quotes”")
    r = build(record((nasty, P), open_findings=(OPEN,)))
    text = export.render_json(r)
    assert json.loads(text) == r and "café" in text and text.endswith("\n")


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.attrs: list[tuple[str, str | None]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.elements.append((tag, dict(attrs)))
        self.attrs.extend(attrs)

    def handle_data(self, data):
        self.text.append(data)


def parse(page: str) -> Collector:
    c = Collector()
    c.feed(page)
    return c


def hostile_report():
    bad = replace(
        F,
        control=replace(F.control, title=f"Title {HOSTILE}"),
        explanation=f"Explanation {HOSTILE}",
        remediation=f"Run `{HOSTILE}` then {HOSTILE}",
        evidence=(Evidence(f"src {HOSTILE}", f"Invalid user {HOSTILE}"),),
    )
    risky = replace(OPEN, finding_id=f"F-{HOSTILE}")
    return export.build_report(
        replace(record((bad, P), open_findings=(risky,)), host_id=f"host {HOSTILE}"),
        (risky,),
        generated_at=WHEN,
        privileged=False,
    )


def test_hostile_scan_text_can_never_become_markup_in_the_html() -> None:
    page = export.render_html(hostile_report())
    parsed = parse(page)
    assert "img" not in parsed.tags and "script" not in parsed.tags
    assert not [a for a, _ in parsed.attrs if a.startswith("on")], "an event handler got through"
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page  # shown literally instead
    assert "steal()" in "".join(parsed.text)  # visible as text, not executed


def test_the_html_is_self_contained_with_a_restrictive_policy() -> None:
    page = export.render_html(build())
    parsed = parse(page)
    assert "script" not in parsed.tags and "iframe" not in parsed.tags and "link" not in parsed.tags
    policies = [
        a["content"]
        for tag, a in parsed.elements
        if tag == "meta" and (a.get("http-equiv") or "").lower() == "content-security-policy"
    ]
    assert policies, "no <meta http-equiv=Content-Security-Policy>: the policy would be inert"
    assert "default-src 'none'" in policies[0]
    links = [v for a, v in parsed.attrs if a in ("href", "src") and v]
    assert all(v.startswith("#") for v in links), links  # only in-page anchors
    assert "http://" not in page and "https://" not in page


def test_the_html_reports_every_control_with_its_evidence_and_word_labels() -> None:
    page = export.render_html(build())
    for cid in ("AUTH-01", "FW-01", "NET-01", "PATCH-01"):
        assert f'id="{cid}"' in page
    for word in ("Passing", "Failing", "Warning", "Not assessed"):
        assert word in page
    assert "Advice for a person. Sentinel AI never applies it." in page
    assert "No AI model generated or altered any content" in page
    assert build()["integrity"]["content_sha256"] in page


def test_status_is_never_colour_alone_each_result_has_its_own_glyph_and_word() -> None:
    glyphs = {export._GLYPH[r] for r in Result}
    assert len(glyphs) == len(Result)
    page = export.render_html(build())
    for result, word in (("fail", "Failing"), ("pass", "Passing")):
        assert f'class="mark {result}"' in page and word in page


def test_backtick_commands_render_as_code_and_are_escaped() -> None:
    cmd = replace(F, remediation="Then run `sudo ufw enable` and `<b>x</b>`.")
    page = export.render_html(build(record((cmd, P), open_findings=(OPEN,))))
    assert "<code>sudo ufw enable</code>" in page and "<code>&lt;b&gt;x&lt;/b&gt;</code>" in page


def test_html_is_deterministic_for_the_same_input_and_time() -> None:
    assert export.render_html(build()) == export.render_html(build())


def test_empty_history_says_so() -> None:
    assert "No findings have been recorded." in export.render_html(
        build(record((P,), open_findings=()), findings=())
    )


# -- files ---------------------------------------------------------------------------


def test_write_report_is_atomic_owner_only_and_picks_the_format_from_the_name(tmp_path) -> None:
    r = build()
    html_path = export.write_report(tmp_path / "out" / "r.html", r)
    json_path = export.write_report(tmp_path / "r.json", r)
    assert html_path.read_text().startswith("<!DOCTYPE html>")
    assert json.loads(json_path.read_text())["schema"] == export.SCHEMA
    for path in (html_path, json_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.rglob(".*.tmp")), "a temp file was left behind"


def test_write_report_replaces_an_existing_file_and_tightens_loose_leftovers(tmp_path) -> None:
    target = tmp_path / "r.json"
    target.write_text("old")
    leftover = tmp_path / ".r.json.tmp"
    leftover.write_text("stale")
    os.chmod(leftover, 0o666)
    export.write_report(target, build())
    assert target.read_text() != "old" and stat.S_IMODE(target.stat().st_mode) == 0o600


def test_unknown_extensions_are_refused_rather_than_guessed(tmp_path) -> None:
    with pytest.raises(ValueError, match=r"\.html or \.json"):
        export.write_report(tmp_path / "report.txt", build())
    assert export.kind_for("A.HTML") == "html" and export.kind_for("x.json") == "json"
    assert export.kind_for("x.pdf") is None
    with pytest.raises(ValueError):
        export.render(build(), "pdf")


def test_default_filename_is_safe_and_descriptive() -> None:
    r = build()
    r["host"]["id"] = "my host/../etc;rm -rf"
    name = export.default_filename(r, "html", now=datetime(2026, 10, 27, 1, 5))
    assert name == "sentinel-report-my-host----etc-rm--rf-20261027-0105.html"
    assert "/" not in name and " " not in name and ".." not in name  # no path traversal
