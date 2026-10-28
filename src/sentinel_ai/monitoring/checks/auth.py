"""AUTH-01: are failed authentication attempts rare, and was the evidence readable?"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

CONTROL = Control(
    "AUTH-01",
    "Unsuccessful authentication attempts are limited and reviewed",
    "NIST SP 800-53 Rev. 5 AC-7 (Unsuccessful Logon Attempts), AU-6 (Audit Record Review), "
    "illustrative",
)
CHECK_ID = "auth_failures"
AUTH_LOG = "/var/log/auth.log"
WINDOW = timedelta(hours=24)
WARN_AT = 5
FAIL_AT = 100
PATTERN = re.compile(
    r"Failed password for|Invalid user|authentication failure|Failed publickey", re.IGNORECASE
)
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?")
_SYSLOG = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}


def parse_log_time(line: str, now: datetime) -> datetime | None:
    """Timestamp of a log line (ISO 8601 or classic syslog), or None if it has neither."""
    iso = _ISO.match(line)
    if iso:
        stamp = datetime.fromisoformat(iso.group(1))
        zone = iso.group(2)
        if zone is None:
            return stamp.replace(tzinfo=now.tzinfo)
        offset = "+00:00" if zone == "Z" else zone
        return datetime.fromisoformat(iso.group(1) + offset)
    old = _SYSLOG.match(line)
    if old and old.group(1) in _MONTHS:
        month, day, hh, mm, ss = _MONTHS[old.group(1)], *map(int, old.groups()[1:])
        year = now.year - (1 if month > now.month else 0)  # a December line seen in January
        try:
            return datetime(year, month, day, hh, mm, ss, tzinfo=now.tzinfo)
        except ValueError:
            return None
    return None


def _program(line: str) -> str:
    # ISO lines: "<ts> <host> <program>[pid]:"; syslog lines: "Mon DD HH:MM:SS <host> <program>:"
    parts = line.split()
    index = 2 if _ISO.match(line) else 4
    token = parts[index] if len(parts) > index else "unknown"
    return re.split(r"[\[:(]", token, maxsplit=1)[0] or "unknown"


def check(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    now = reader.now()
    since = now - WINDOW

    source = AUTH_LOG
    try:
        text = reader.read_tail(AUTH_LOG)
    except OSError as exc:
        ev.append(Evidence(AUTH_LOG, f"not readable: {exc.strerror or exc}"))
        proc = reader.run(
            ["journalctl", "--no-pager", "-q", "--since", "24 hours ago", "-g", PATTERN.pattern]
        )
        if proc.returncode not in (0, 1):  # grep-style: 1 means "no matches"
            ev.append(Evidence("journalctl", f"unavailable: {proc.stderr.strip()[:120]}"))
            return Assessment(
                CHECK_ID,
                CONTROL,
                Result.UNKNOWN,
                Severity.INFO,
                "Neither the authentication log nor the journal could be read, so failed "
                "logins cannot be counted. Membership of the 'adm' group grants read access "
                "to /var/log/auth.log.",
                tuple(ev),
            )
        source, text = "journalctl (last 24 hours)", proc.stdout

    matches: list[str] = []
    for line in text.splitlines():
        stamp = parse_log_time(line, now)
        if stamp is not None and stamp >= since and PATTERN.search(line):
            matches.append(line)

    count = len(matches)
    by_program: dict[str, int] = {}
    for line in matches:
        prog = _program(line)
        by_program[prog] = by_program.get(prog, 0) + 1
    breakdown = ", ".join(f"{p}: {n}" for p, n in sorted(by_program.items(), key=lambda x: -x[1]))
    ev.append(Evidence(source, f"{count} failed-authentication lines in the last 24 hours"))
    if breakdown:
        ev.append(Evidence(source, f"by program: {breakdown}"))
    for line in matches[:3]:
        ev.append(Evidence(source, f"sample: {line[:160]}"))

    if count >= FAIL_AT:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.FAIL,
            Severity.HIGH,
            f"{count} failed authentication attempts in the last 24 hours, far more than "
            "mistyped passwords explain. This pattern is consistent with password guessing.",
            tuple(ev),
            "Identify the source in the log. If SSH is involved, disable password login, "
            "restrict access with the firewall, and consider fail2ban.",
        )
    if count >= WARN_AT:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.WARNING,
            Severity.MEDIUM,
            f"{count} failed authentication attempts in the last 24 hours. A few are normal; "
            "check that they were you.",
            tuple(ev),
            f"Review them with `grep -E 'Failed|Invalid user|authentication failure' {AUTH_LOG}`.",
        )
    return Assessment(
        CHECK_ID,
        CONTROL,
        Result.PASS,
        Severity.INFO,
        f"{count} failed authentication attempts in the last 24 hours, below the review "
        f"threshold of {WARN_AT}.",
        tuple(ev),
    )
