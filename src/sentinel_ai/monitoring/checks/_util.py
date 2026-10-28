"""Small helpers shared by the checks."""

from __future__ import annotations

import re

from sentinel_ai.monitoring.model import Evidence
from sentinel_ai.monitoring.reader import Reader


def read_file(reader: Reader, path: str, evidence: list[Evidence]) -> str | None:
    """Read a file, recording WHY if it could not be read. Returns None on failure."""
    try:
        return reader.read_text(path)
    except FileNotFoundError:
        evidence.append(Evidence(path, "file does not exist"))
    except PermissionError:
        evidence.append(Evidence(path, "permission denied (not readable without elevated rights)"))
    except OSError as exc:
        evidence.append(Evidence(path, f"could not be read: {exc.strerror or exc}"))
    return None


def systemd_state(reader: Reader, unit: str, evidence: list[Evidence]) -> str | None:
    """`systemctl is-active <unit>`: 'active', 'inactive', 'failed'... or None if unavailable."""
    proc = reader.run(["systemctl", "is-active", unit])
    state = proc.stdout.strip()
    if proc.returncode == 127 or not state:
        evidence.append(
            Evidence(f"systemctl is-active {unit}", f"unavailable: {proc.stderr.strip()}")
        )
        return None
    evidence.append(Evidence(f"systemctl is-active {unit}", state))
    return state


def config_value(text: str, key: str) -> str | None:
    """Last `KEY=value` assignment in a shell-style file, quotes stripped; None if absent."""
    found = None
    for line in text.splitlines():
        match = re.match(rf"\s*{re.escape(key)}\s*=\s*(.*?)\s*(?:#.*)?$", line)
        if match:
            found = match.group(1).strip().strip("'\"")
    return found
