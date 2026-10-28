"""FW-01: is a host firewall enabled and denying inbound traffic by default?

What an unprivileged process can and cannot see drives the logic (see docs/ASSESSMENT.md):
`ufw status` and the live rules need root, but ufw's configuration and the systemd state are
readable. The configuration is authoritative for "is it enabled": a ufw *service* can report
active while ENABLED=no, in which case no rules are enforced. Trusting the service state
alone would produce a false PASS, which is exactly what this check exists to avoid.
"""

from __future__ import annotations

from sentinel_ai.monitoring.checks._util import config_value, read_file, systemd_state
from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

CONTROL = Control(
    "FW-01",
    "Host firewall is enabled and denies inbound traffic by default",
    "NIST SP 800-53 Rev. 5 SC-7 (Boundary Protection), illustrative",
)
CHECK_ID = "firewall"

UFW_CONF = "/etc/ufw/ufw.conf"
UFW_DEFAULTS = "/etc/default/ufw"
DENY_POLICIES = {"DROP", "REJECT"}
ENABLE_ADVICE = (
    "Allow the services you need first, then enable the firewall. For example: "
    "`sudo ufw default deny incoming`, `sudo ufw allow <service>` for anything you rely on "
    "(for SSH: `sudo ufw allow OpenSSH`), then `sudo ufw enable`. Verify with "
    "`sudo ufw status verbose`."
)


def _result(
    result: Result,
    severity: Severity,
    explanation: str,
    evidence: list[Evidence],
    remediation: str | None = None,
) -> Assessment:
    return Assessment(
        CHECK_ID, CONTROL, result, severity, explanation, tuple(evidence), remediation
    )


def check(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    installed = reader.exists("/usr/sbin/ufw") or reader.exists(UFW_CONF)
    if not installed:
        return _no_ufw(reader, ev)

    conf = read_file(reader, UFW_CONF, ev)
    if conf is None:
        return _result(
            Result.UNKNOWN,
            Severity.INFO,
            "UFW appears to be installed but its configuration could not be read, so the "
            "firewall state cannot be assessed.",
            ev,
        )
    enabled = (config_value(conf, "ENABLED") or "").lower()
    ev.append(Evidence(UFW_CONF, f"ENABLED={enabled or '(not set)'}"))
    service = systemd_state(reader, "ufw", ev)

    live = reader.run(["ufw", "status"])
    live_readable = live.returncode == 0
    live_status = ""
    if live_readable:
        live_status = next((ln for ln in live.stdout.splitlines() if ln.strip()), "")
        ev.append(Evidence("ufw status", live_status))
    else:
        ev.append(
            Evidence(
                "ufw status",
                "not readable without root; live rules were not inspected "
                f"(exit {live.returncode})",
            )
        )

    defaults = read_file(reader, UFW_DEFAULTS, ev)
    policy = (config_value(defaults, "DEFAULT_INPUT_POLICY") or "").upper() if defaults else ""
    if defaults is not None:
        ev.append(Evidence(UFW_DEFAULTS, f"DEFAULT_INPUT_POLICY={policy or '(not set)'}"))

    if enabled != "yes":
        conflict = (
            " The ufw service reports 'active', but an active service does not mean rules are "
            "loaded: the configuration says the firewall is not enabled, so it is treated as "
            "not enforcing."
            if service == "active"
            else ""
        )
        return _result(
            Result.FAIL,
            Severity.HIGH,
            f"UFW is configured as disabled (ENABLED={enabled or 'not set'}).{conflict}",
            ev,
            ENABLE_ADVICE,
        )
    if service != "active":
        return _result(
            Result.FAIL,
            Severity.HIGH,
            f"UFW is configured as enabled but the ufw service is '{service or 'unknown'}', so "
            "rules may not be loaded.",
            ev,
            "Start and enable the service: `sudo systemctl enable --now ufw`, then check "
            "`sudo ufw status verbose`.",
        )
    if live_readable and "inactive" in live_status.lower():
        return _result(
            Result.FAIL,
            Severity.HIGH,
            "UFW is configured as enabled, but `ufw status` reports it inactive.",
            ev,
            ENABLE_ADVICE,
        )
    if defaults is None or not policy:
        return _result(
            Result.WARNING,
            Severity.LOW,
            "UFW is enabled and its service is active, but the default inbound policy could "
            "not be determined.",
            ev,
            "Check `sudo ufw status verbose` and confirm 'Default: deny (incoming)'.",
        )
    if policy not in DENY_POLICIES:
        return _result(
            Result.FAIL,
            Severity.MEDIUM,
            f"UFW is enabled, but the default inbound policy is {policy}, so unlisted inbound "
            "traffic is not blocked.",
            ev,
            "Set a deny-by-default policy: `sudo ufw default deny incoming`.",
        )
    limit = (
        ""
        if live_readable
        else " The live ruleset requires root and was not inspected, so this confirms the "
        "firewall's configured state, not the individual rules."
    )
    return _result(
        Result.PASS,
        Severity.INFO,
        f"UFW is enabled, its service is active and the default inbound policy is {policy}.{limit}",
        ev,
    )


def _no_ufw(reader: Reader, ev: list[Evidence]) -> Assessment:
    ev.append(Evidence(UFW_CONF, "UFW is not installed (no /usr/sbin/ufw or /etc/ufw/ufw.conf)"))
    active = []
    for unit in ("nftables", "firewalld"):
        if systemd_state(reader, unit, ev) == "active":
            active.append(unit)
    if active:
        return _result(
            Result.UNKNOWN,
            Severity.INFO,
            f"UFW is not installed, but {', '.join(active)} is active. Its ruleset needs root "
            "to read, so whether it denies inbound traffic by default cannot be assessed here.",
            ev,
        )
    return _result(
        Result.WARNING,
        Severity.MEDIUM,
        "No host firewall service was detected (UFW is not installed; nftables and firewalld "
        "are not active). Rules could still be loaded some other way, which cannot be "
        "checked without root.",
        ev,
        "Install and enable a firewall, for example `sudo apt install ufw`, then follow the "
        "steps in the UFW documentation to allow what you need and enable it.",
    )
