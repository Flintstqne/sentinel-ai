"""The registry of checks. Each is `check(reader) -> Assessment`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sentinel_ai.monitoring.checks import auth, firewall, ports, ssh, updates
from sentinel_ai.monitoring.model import Assessment, Control
from sentinel_ai.monitoring.reader import Reader


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    control: Control  # needed so a crashed check can still be reported against its control
    run: Callable[[Reader], Assessment]


# Firewall first: it is the headline control.
CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec("firewall", firewall.CONTROL, firewall.check),
    CheckSpec("ssh_exposure", ssh.EXPOSURE, ssh.check_exposure),
    CheckSpec("ssh_hardening", ssh.HARDENING, ssh.check_hardening),
    CheckSpec("auth_failures", auth.CONTROL, auth.check),
    CheckSpec("security_updates", updates.CONTROL, updates.check),
    CheckSpec("listening_ports", ports.CONTROL, ports.check),
)
