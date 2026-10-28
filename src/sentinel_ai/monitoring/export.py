"""Audit evidence reports: one data model, rendered as JSON or as a self-contained HTML page.

Design rules
* The report contains every control of the scan, passes included, each with the evidence it
  rests on. An auditor needs proof that something was checked, not only a list of problems.
* Finding history (open, acknowledged and resolved) is included.
* The content hash covers the assessment itself, not the time of export, so the same scan
  always hashes the same. It is a checksum that detects accidental change; it is not a digital
  signature and the report says so.
* The HTML is one file: no scripts, no external resources, a restrictive content-security
  policy, and every piece of scan text escaped (evidence can contain attacker-controlled
  strings such as user names from failed-login log lines).
* Nothing in a report was written by an AI: every sentence comes from a deterministic check.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel_ai import __version__
from sentinel_ai.monitoring.model import Assessment, Finding, Result
from sentinel_ai.monitoring.store import ScanRecord
from sentinel_ai.monitoring.summary import RESULT_LABEL, headline

SCHEMA = "sentinel-ai/report/1"

LIMITATIONS = (
    "Evidence was collected read-only as an unprivileged user. Anything that needs root (the "
    "live firewall ruleset, `sshd -T`, the owning process of other users' sockets) was not "
    "inspected, and each affected control says so in its evidence.",
    "Results describe the moment of the scan. They are a point-in-time assessment, not "
    "continuous-monitoring evidence.",
    "Framework references are an illustrative mapping of each control, not a compliance "
    "attestation.",
    "Every assessment, explanation and recommendation was produced by a deterministic check. "
    "No AI model generated or altered any content in this report.",
    "The content checksum detects accidental change. It is not a digital signature and does "
    "not prove who produced the report.",
)

_GLYPH = {
    Result.PASS: "✔",
    Result.FAIL: "✖",
    Result.WARNING: "▲",
    Result.UNKNOWN: "?",
    Result.ERROR: "!",
}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence(a: Assessment | Finding) -> list[dict[str, str]]:
    return [{"source": e.source, "observation": e.observation} for e in a.evidence]


def _finding(f: Finding) -> dict[str, Any]:
    return {
        "finding_id": f.finding_id,
        "control_id": f.control_id,
        "severity": f.severity.name.lower(),
        "result": f.result.value,
        "status": f.resolution_status.value,
        "detected_at": f.detected_at,
        "last_seen_at": f.last_seen_at,
        "resolved_at": f.resolved_at,
        "explanation": f.explanation,
        "remediation": f.remediation,
        "evidence": _evidence(f),
    }


def canonical(obj: Any) -> str:
    """Stable serialisation (sorted keys, no whitespace) used for the content hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_report(
    record: ScanRecord,
    findings: Sequence[Finding],
    generated_at: datetime | None = None,
    privileged: bool | None = None,
) -> dict[str, Any]:
    open_by_control = {f.control_id: f for f in findings if f.resolution_status.value != "resolved"}
    controls = []
    for a in sorted(record.assessments, key=lambda x: x.control.control_id):
        current = open_by_control.get(a.control.control_id)
        controls.append(
            {
                "check_id": a.check_id,
                "control_id": a.control.control_id,
                "title": a.control.title,
                "reference": a.control.reference,
                "result": a.result.value,
                "severity": a.severity.name.lower(),
                "explanation": a.explanation,
                "remediation": a.remediation,
                "evidence": _evidence(a),
                "finding_id": current.finding_id if current else None,
            }
        )
    counts = {r.value: 0 for r in Result}
    for a in record.assessments:
        counts[a.result.value] += 1
    _, title, subtitle = headline(record)
    scan = {
        "scan_id": record.scan_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "read_only": True,
        "privileged": (os.geteuid() == 0) if privileged is None else privileged,
    }
    host = {"id": record.host_id}
    finding_list = [
        _finding(f) for f in sorted(findings, key=lambda f: (f.detected_at, f.finding_id))
    ]
    body = {"host": host, "scan": scan, "controls": controls, "findings": finding_list}
    return {
        "schema": SCHEMA,
        "generated_at": _iso(generated_at or datetime.now(UTC)),
        "tool": {"name": "Sentinel AI", "version": __version__},
        **body,
        "summary": {
            "headline": title,
            "detail": subtitle,
            "counts": counts,
            "controls_total": len(controls),
            "open_findings": len(open_by_control),
        },
        "limitations": list(LIMITATIONS),
        "integrity": {
            "algorithm": "sha256",
            "content_sha256": hashlib.sha256(canonical(body).encode("utf-8")).hexdigest(),
            "covers": "host, scan, controls and findings (not the export time)",
        },
    }


def verify_integrity(report: dict[str, Any]) -> bool:
    """True if the report's content still matches its recorded checksum."""
    body = {k: report[k] for k in ("host", "scan", "controls", "findings")}
    digest = hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
    return digest == report["integrity"]["content_sha256"]


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


# -- HTML ----------------------------------------------------------------------------

_CSS = """
:root{--ink:#1b1f25;--muted:#5b6573;--line:#d9dee5;--wash:#f4f6f8;--good:#2e8b57;
--warning:#b9770e;--danger:#c0392b;--neutral:#6b7686;--accent:#1f7a73}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,
-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:40px 28px 64px}
header{border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:28px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:0 0 4px}
.muted{color:var(--muted)}.small{font-size:13px}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 18px;font-size:13px;
margin-top:12px}
.meta dt{color:var(--muted)}.meta dd{margin:0}
.lead{font-size:20px;font-weight:600;margin:0}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0 0}
.tile{border:1px solid var(--line);padding:10px 12px;background:var(--wash)}
.tile b{display:block;font-size:26px;line-height:1.1}.tile span{font-size:12px;color:var(--muted)}
.mark{display:inline-block;width:1.2em;text-align:center;font-weight:700}
.pass{color:var(--good)}.fail{color:var(--danger)}.warning{color:var(--warning)}
.unknown,.error{color:var(--neutral)}
.control{border:1px solid var(--line);padding:18px 20px;margin:0 0 16px;break-inside:avoid}
.chips{margin:6px 0 10px}.chip{display:inline-block;border:1px solid var(--line);
padding:1px 8px;margin-right:6px;font-size:12px;background:var(--wash)}
.label{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
font-weight:600;margin:14px 0 4px}
.evidence{background:var(--wash);border:1px solid var(--line);padding:4px 12px}
.evidence td{border:none;padding:5px 8px 5px 0}
.evidence td:first-child{width:34%;color:var(--muted)}
ul{margin:6px 0;padding-left:20px}footer{margin-top:40px;padding-top:14px;
border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
@media(max-width:700px){.tiles{grid-template-columns:repeat(2,1fr)}}
@media print{main{padding:0}.control{page-break-inside:avoid}a{color:inherit}}
""".strip()


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _mark(result: str) -> str:
    glyph = _GLYPH[Result(result)]
    return f'<span class="mark {_e(result)}" aria-hidden="true">{glyph}</span>'


def _status(result: str) -> str:
    # The word carries the meaning; the coloured glyph only reinforces it.
    return f"{_mark(result)}{_e(RESULT_LABEL[Result(result)])}"


def _stamp(value: str | None) -> str:
    return _e(value.replace("T", " ").replace("Z", " UTC")) if value else "&ndash;"


def _commands(text: str) -> str:
    """Escape text and render `backtick` spans as <code>."""
    parts = text.split("`")
    return "".join(f"<code>{_e(p)}</code>" if i % 2 else _e(p) for i, p in enumerate(parts))


def _control_html(c: dict[str, Any], finding: dict[str, Any] | None) -> str:
    result = Result(c["result"])
    sev = c["severity"]
    chips = [f'<span class="chip">{_e(c["control_id"])}</span>']
    if result in (Result.FAIL, Result.WARNING):
        chips.append(f'<span class="chip">{_e(sev.title())} severity</span>')
    rows = "".join(
        f'<tr><td class="mono">{_e(e["source"])}</td>'
        f'<td class="mono">{_e(e["observation"])}</td></tr>'
        for e in c["evidence"]
    )
    evidence = (
        f'<div class="label">Evidence</div><table class="evidence">{rows}</table>'
        if rows
        else '<div class="label">Evidence</div><p class="muted small">None collected.</p>'
    )
    action = (
        f'<div class="label">Recommended action</div><p>{_commands(c["remediation"])}</p>'
        '<p class="muted small">Advice for a person. Sentinel AI never applies it.</p>'
        if c["remediation"]
        else ""
    )
    history = ""
    if finding:
        history = (
            '<div class="label">Finding</div>'
            f'<p><span class="mono">{_e(finding["finding_id"])}</span> &middot; '
            f"{_e(finding['status'].title())}<br>"
            f'<span class="muted small">First seen {_stamp(finding["detected_at"])} &middot; '
            f"last seen {_stamp(finding['last_seen_at'])}</span></p>"
        )
    return (
        f'<section class="control" id="{_e(c["control_id"])}">'
        f"<h3>{_status(c['result'])} &nbsp;{_e(c['title'])}</h3>"
        f'<div class="chips">{"".join(chips)}</div>'
        f'<div class="label">What was found</div><p>{_e(c["explanation"])}</p>'
        f"{evidence}{action}{history}"
        f'<div class="label">Reference</div><p class="muted small">{_e(c["reference"])}</p>'
        "</section>"
    )


def render_html(report: dict[str, Any]) -> str:
    s, scan, host = report["summary"], report["scan"], report["host"]
    findings = {f["finding_id"]: f for f in report["findings"]}
    counts = s["counts"]
    tiles = "".join(
        f'<div class="tile"><b>{_e(counts[key])}</b><span>{_e(label)}</span></div>'
        for key, label in (
            ("pass", "Passing"),
            ("warning", "Warnings"),
            ("fail", "Failing"),
            ("unknown", "Not assessed"),
            ("error", "Check errors"),
        )
    )
    overview = "".join(
        "<tr>"
        f'<td><a href="#{_e(c["control_id"])}">{_e(c["control_id"])}</a></td>'
        f"<td>{_e(c['title'])}</td><td>{_status(c['result'])}</td>"
        f"<td>{_e(c['severity'].title()) if c['result'] in ('fail', 'warning') else '&ndash;'}</td>"
        "</tr>"
        for c in report["controls"]
    )
    sections = "".join(
        _control_html(c, findings.get(c["finding_id"] or "")) for c in report["controls"]
    )
    history_rows = "".join(
        "<tr>"
        f'<td class="mono">{_e(f["finding_id"])}</td><td>{_e(f["control_id"])}</td>'
        f"<td>{_e(f['severity'].title())}</td><td>{_e(f['status'].title())}</td>"
        f"<td>{_stamp(f['detected_at'])}</td><td>{_stamp(f['resolved_at'])}</td></tr>"
        for f in report["findings"]
    )
    history = (
        "<h2>Finding history</h2><table><thead><tr><th>Finding</th><th>Control</th>"
        "<th>Severity</th><th>Status</th><th>First seen</th><th>Resolved</th></tr></thead>"
        f"<tbody>{history_rows}</tbody></table>"
        if history_rows
        else '<h2>Finding history</h2><p class="muted">No findings have been recorded.</p>'
    )
    limits = "".join(f"<li>{_commands(item)}</li>" for item in report["limitations"])
    privilege = "Yes (root)" if scan["privileged"] else "No (unprivileged user)"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Security assessment: {_e(host['id'])}</title>"
        f"<style>{_CSS}</style></head><body><main>"
        "<header><h1>Security assessment report</h1>"
        '<p class="muted" style="margin:0">Linux host security controls, collected by '
        "Sentinel AI</p>"
        '<dl class="meta">'
        f"<dt>Host</dt><dd>{_e(host['id'])}</dd>"
        f"<dt>Scan</dt><dd>#{_e(scan['scan_id'])}, {_stamp(scan['started_at'])} to "
        f"{_stamp(scan['finished_at'])}</dd>"
        f"<dt>Collected</dt><dd>Read-only; privileged: {_e(privilege)}</dd>"
        f"<dt>Generated</dt><dd>{_stamp(report['generated_at'])} by "
        f"{_e(report['tool']['name'])} {_e(report['tool']['version'])}</dd>"
        f'<dt>Content SHA-256</dt><dd class="mono">{_e(report["integrity"]["content_sha256"])}</dd>'
        "</dl></header>"
        "<h2>Summary</h2>"
        f'<p class="lead">{_e(s["headline"])}</p><p class="muted">{_e(s["detail"])}</p>'
        f'<div class="tiles">{tiles}</div>'
        "<h2>Controls</h2><table><thead><tr><th>ID</th><th>Control</th><th>Result</th>"
        f"<th>Severity</th></tr></thead><tbody>{overview}</tbody></table>"
        "<h2>Detail and evidence</h2>"
        f"{sections}{history}"
        f"<h2>Scope and limitations</h2><ul>{limits}</ul>"
        f"<footer>Generated by Sentinel AI {_e(report['tool']['version'])}. Schema "
        f"{_e(report['schema'])}. This page contains no scripts and loads no external "
        "resources.</footer></main></body></html>\n"
    )


# -- files ---------------------------------------------------------------------------

KINDS = {".html": "html", ".htm": "html", ".json": "json"}


def kind_for(path: Path | str) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def render(report: dict[str, Any], kind: str) -> str:
    if kind == "json":
        return render_json(report)
    if kind == "html":
        return render_html(report)
    raise ValueError(f"unknown report kind: {kind!r}")


def default_filename(report: dict[str, Any], kind: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    host = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in report["host"]["id"])
    return f"sentinel-report-{host}-{stamp}.{'html' if kind == 'html' else 'json'}"


def export_latest(store: Any, host_id: str, path: Path | str) -> Path:
    """Write the most recent stored scan for host_id. LookupError if nothing was scanned yet."""
    record = store.latest_scan(host_id)
    if record is None:
        raise LookupError("no scan has been recorded yet; run a scan first")
    return write_report(path, build_report(record, store.all_findings(host_id)))


def write_report(path: Path | str, report: dict[str, Any], kind: str | None = None) -> Path:
    """Write atomically with owner-only permissions: evidence can name users and addresses."""
    target = Path(path)
    chosen = kind or kind_for(target)
    if chosen is None:
        raise ValueError(f"cannot tell the report format from {target.name!r}: use .html or .json")
    data = render(report, chosen)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)  # a leftover temp file may have had looser permissions
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, target)
    return target
