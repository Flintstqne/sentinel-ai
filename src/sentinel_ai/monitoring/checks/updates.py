"""PATCH-01: are security updates pending, and is the data trustworthy?"""

from __future__ import annotations

import re
from datetime import timedelta

from sentinel_ai.monitoring.checks._util import read_file
from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

CONTROL = Control(
    "PATCH-01",
    "Security updates are applied promptly",
    "NIST SP 800-53 Rev. 5 SI-2 (Flaw Remediation), illustrative",
)
CHECK_ID = "security_updates"
UPDATES_FILE = "/var/lib/update-notifier/updates-available"
MAX_AGE = timedelta(days=7)
_TOTAL = re.compile(r"(\d+)\s+updates?\s+can\s+be\s+applied\s+immediately")
_SECURITY = re.compile(r"(\d+)\s+of\s+these\s+updates?\s+(?:is|are)\s+(?:a\s+)?standard\s+security")


def check(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    text = read_file(reader, UPDATES_FILE, ev)
    if text is None:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.UNKNOWN,
            Severity.INFO,
            "The update-notifier summary is not available, so pending security updates "
            "cannot be assessed.",
            tuple(ev),
        )
    modified = reader.mtime(UPDATES_FILE)
    age = reader.now() - modified if modified else None
    ev.append(
        Evidence(
            UPDATES_FILE,
            f"last updated {modified:%Y-%m-%d %H:%M}" if modified else "modification time unknown",
        )
    )

    total_m, sec_m = _TOTAL.search(text), _SECURITY.search(text)
    total = int(total_m.group(1)) if total_m else None
    # The notifier omits the security line when there are none, so absence means zero
    # only if the file clearly reported a total.
    security = int(sec_m.group(1)) if sec_m else (0 if total is not None else None)
    ev.append(
        Evidence(
            UPDATES_FILE,
            f"updates available: {total if total is not None else 'not stated'}; "
            f"security updates: {security if security is not None else 'not stated'}",
        )
    )

    if age is None or age > MAX_AGE:
        shown = f"{age.days} days" if age else "an unknown time"
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.UNKNOWN,
            Severity.INFO,
            f"The update summary was last refreshed {shown} ago, too old to trust.",
            tuple(ev),
            "Refresh it with `sudo apt update`.",
        )
    if security is None:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.UNKNOWN,
            Severity.INFO,
            "The update summary did not state how many updates are available.",
            tuple(ev),
        )
    if security > 0:
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.WARNING,
            Severity.MEDIUM,
            f"{security} security update{'s' if security != 1 else ''} pending.",
            tuple(ev),
            "Apply them with `sudo apt update && sudo apt upgrade`, or use the Update Manager.",
        )
    return Assessment(
        CHECK_ID,
        CONTROL,
        Result.PASS,
        Severity.INFO,
        "No security updates are pending according to a summary refreshed within the last "
        f"{MAX_AGE.days} days.",
        tuple(ev),
    )
