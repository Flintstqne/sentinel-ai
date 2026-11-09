"""SIEM-01: does the Wazuh manager report recent high-level alerts, and is that data live?

Wazuh writes one JSON alert per line to alerts.json. Every field in an alert can be
influenced by whoever caused it (a username, a URL, a log line), so it is treated as
untrusted text: truncated, stripped of control characters, never interpreted as markup.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

CONTROL = Control(
    "SIEM-01",
    "Security events are centrally monitored and high-severity alerts are reviewed",
    "NIST SP 800-53 Rev. 5 SI-4 (System Monitoring), AU-6 (Audit Record Review), illustrative",
)
CHECK_ID = "wazuh_alerts"
OSSEC_DIR = "/var/ossec"
ALERTS_FILE = "/var/ossec/logs/alerts/alerts.json"
WINDOW = timedelta(hours=24)
MAX_AGE = timedelta(hours=24)  # an alerts file untouched this long means the manager is not live
WARN_LEVEL = 7
FAIL_LEVEL = 12
MAX_SAMPLES = 5
MAX_TEXT = 160


def severity_for(level: int) -> Severity:
    """Wazuh rule levels run 0-15. The mapping table is in docs/CHECKS.md."""
    if level >= 15:
        return Severity.CRITICAL
    if level >= FAIL_LEVEL:
        return Severity.HIGH
    if level >= 10:
        return Severity.MEDIUM
    if level >= WARN_LEVEL:
        return Severity.LOW
    return Severity.INFO


def _clean(value: object) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return text[:MAX_TEXT]


def _parse_time(stamp: str) -> datetime | None:
    # Wazuh writes "2026-11-09T10:00:00.123+0000"; fromisoformat accepts that from 3.11.
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def _unknown(explanation: str, ev: list[Evidence]) -> Assessment:
    return Assessment(CHECK_ID, CONTROL, Result.UNKNOWN, Severity.INFO, explanation, tuple(ev))


def check(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    if not reader.exists(OSSEC_DIR):
        ev.append(Evidence(OSSEC_DIR, "does not exist"))
        return _unknown(
            "Wazuh is not installed on this host, so no SIEM alerts are available. "
            "This check cannot pass without a live alert feed.",
            ev,
        )
    try:
        text = reader.read_tail(ALERTS_FILE)
    except FileNotFoundError:
        ev.append(Evidence(ALERTS_FILE, "file does not exist"))
        return _unknown(
            "Wazuh is present but has no alerts file. This host may run only the agent, "
            "with alerts kept on the manager.",
            ev,
        )
    except OSError as exc:
        ev.append(Evidence(ALERTS_FILE, f"not readable: {exc.strerror or exc}"))
        return _unknown(
            "The Wazuh alerts file could not be read. Membership of the 'wazuh' group grants "
            "read access.",
            ev,
        )

    now = reader.now()
    modified = reader.mtime(ALERTS_FILE)
    ev.append(
        Evidence(
            ALERTS_FILE,
            f"last written {modified:%Y-%m-%d %H:%M}" if modified else "modification time unknown",
        )
    )
    if modified is None or now - modified > MAX_AGE:
        return _unknown(
            "The Wazuh alerts file has not been written in over 24 hours, so the manager may "
            "not be running. Silence from a dead feed is not evidence of safety.",
            ev,
        )

    since = now - WINDOW
    events = 0
    baseline = 0  # SCA results: configuration-baseline checks, not security events
    rules: dict[str, list] = {}  # rule id -> [max level, count, latest stamp, observation]
    malformed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            alert = json.loads(line)
            rule = alert["rule"]
            level = int(rule["level"])
            stamp = _parse_time(alert["timestamp"])
            agent = (alert.get("agent") or {}).get("name", "?")
            description, rule_id = rule.get("description", ""), rule.get("id", "?")
            groups = rule.get("groups") or []
            is_sca = isinstance(groups, list) and "sca" in groups
        except (ValueError, KeyError, TypeError, AttributeError):
            malformed += 1
            continue
        if stamp is None:
            malformed += 1
            continue
        if stamp < since:
            continue
        if is_sca:
            baseline += 1
            continue
        events += 1
        if level < WARN_LEVEL:
            continue
        observation = (
            f"{_clean(description)} (agent {_clean(agent)}, latest {_clean(alert['timestamp'])})"
        )
        entry = rules.setdefault(_clean(rule_id), [level, 0, stamp, observation])
        entry[1] += 1
        if level > entry[0]:
            entry[0] = level
        if stamp >= entry[2]:
            entry[2], entry[3] = stamp, observation

    # Worst level first, then the noisiest rule.
    flagged = sorted(rules.items(), key=lambda item: (-item[1][0], -item[1][1]))
    total_flagged = sum(entry[1] for _, entry in flagged)
    ev.append(
        Evidence(
            ALERTS_FILE,
            f"{events} security events in the last 24 hours, {total_flagged} at level "
            f"{WARN_LEVEL} or above from {len(flagged)} rule{'s' if len(flagged) != 1 else ''}",
        )
    )
    if baseline:
        ev.append(
            Evidence(
                ALERTS_FILE,
                f"{baseline} configuration-assessment (SCA) results not judged here: they are "
                "baseline checks, not security events",
            )
        )
    if malformed:
        ev.append(Evidence(ALERTS_FILE, f"{malformed} lines could not be parsed and were skipped"))
    for rule_id, (level, count, _stamp, observation) in flagged[:MAX_SAMPLES]:
        ev.append(
            Evidence(f"{ALERTS_FILE} rule {rule_id}", f"level {level}, x{count}: {observation}")
        )

    if not flagged:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.PASS,
            Severity.INFO,
            f"The Wazuh feed is live and raised no security events at level {WARN_LEVEL} or "
            "above in the last 24 hours.",
            tuple(ev),
        )
    top = flagged[0][1][0]
    result = Result.FAIL if top >= FAIL_LEVEL else Result.WARNING
    return Assessment(
        CHECK_ID,
        CONTROL,
        result,
        severity_for(top),
        f"Wazuh raised {total_flagged} security event{'s' if total_flagged != 1 else ''} at "
        f"level {WARN_LEVEL} or above in the last 24 hours, from {len(flagged)} "
        f"rule{'s' if len(flagged) != 1 else ''}; the highest was level {top}.",
        tuple(ev),
        "Review the listed rules in the Wazuh dashboard or alerts.json and confirm each "
        "was expected.",
    )
