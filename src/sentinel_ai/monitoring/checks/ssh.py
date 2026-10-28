"""SSH-01 (is an SSH server exposed?) and SSH-02 (is its configuration hardened?)."""

from __future__ import annotations

from sentinel_ai.monitoring.checks._util import read_file, systemd_state
from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

EXPOSURE = Control(
    "SSH-01",
    "SSH server exposure is known and intended",
    "NIST SP 800-53 Rev. 5 CM-7 (Least Functionality), AC-17 (Remote Access), illustrative",
)
HARDENING = Control(
    "SSH-02",
    "SSH server configuration is hardened",
    "NIST SP 800-53 Rev. 5 AC-17 (Remote Access), IA-5 (Authenticator Management), illustrative",
)

SSHD_BINARY = "/usr/sbin/sshd"
SSHD_CONFIG = "/etc/ssh/sshd_config"
MAX_INCLUDE_DEPTH = 3
# OpenSSH defaults when a directive is absent.
DEFAULTS = {
    "permitrootlogin": "prohibit-password",
    "passwordauthentication": "yes",
    "permitemptypasswords": "no",
}


def sshd_installed(reader: Reader) -> bool:
    return reader.exists(SSHD_BINARY) or reader.exists(SSHD_CONFIG)


def check_exposure(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    installed = sshd_installed(reader)
    ev.append(
        Evidence(
            f"{SSHD_BINARY}, {SSHD_CONFIG}",
            "an SSH server is installed" if installed else "no SSH server is installed",
        )
    )
    if not installed:
        return Assessment(
            "ssh_exposure",
            EXPOSURE,
            Result.PASS,
            Severity.INFO,
            "No SSH server is installed, so nothing is offering remote shell access.",
            tuple(ev),
        )

    states = {unit: systemd_state(reader, unit, ev) for unit in ("ssh", "ssh.socket")}
    ports = reader.run(["ss", "-tlnH"])
    listeners = [ln.split()[3] for ln in ports.stdout.splitlines() if len(ln.split()) >= 4]
    on_22 = [addr for addr in listeners if addr.rsplit(":", 1)[-1] == "22"]
    if ports.returncode == 0:
        ev.append(Evidence("ss -tlnH", f"TCP listeners on port 22: {', '.join(on_22) or 'none'}"))
    else:
        ev.append(Evidence("ss -tlnH", f"unavailable: {ports.stderr.strip()}"))

    running = any(s == "active" for s in states.values()) or bool(on_22)
    if running:
        where = f" (listening on {', '.join(on_22)})" if on_22 else ""
        return Assessment(
            "ssh_exposure",
            EXPOSURE,
            Result.WARNING,
            Severity.LOW,
            f"An SSH server is running{where}. That is fine if intended, but it is remote "
            "access to this machine and should be a deliberate choice.",
            tuple(ev),
            "If you do not need remote access: `sudo systemctl disable --now ssh ssh.socket`. "
            "If you do, keep SSH-02 passing and allow it through the firewall deliberately.",
        )
    if ports.returncode != 0:
        return Assessment(
            "ssh_exposure",
            EXPOSURE,
            Result.UNKNOWN,
            Severity.INFO,
            "An SSH server is installed and its services report inactive, but listening ports "
            "could not be read to confirm nothing is on port 22.",
            tuple(ev),
        )
    return Assessment(
        "ssh_exposure",
        EXPOSURE,
        Result.PASS,
        Severity.INFO,
        "An SSH server is installed but not running: its services are inactive and nothing "
        "is listening on port 22.",
        tuple(ev),
    )


def parse_sshd_config(
    reader: Reader, path: str, ev: list[Evidence], depth: int = 0
) -> dict[str, str] | None:
    """Effective global directives. First value wins (OpenSSH semantics); stops at `Match`.

    This is a static read, not `sshd -T` (which needs root): `Match` blocks and
    command-line overrides are not evaluated.
    """
    text = read_file(reader, path, ev)
    if text is None:
        return None
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        key, value = key.lower(), value.strip()
        if key == "match":
            break  # everything after is conditional
        if key == "include" and depth < MAX_INCLUDE_DEPTH:
            for pattern in value.split():
                full = pattern if pattern.startswith("/") else f"/etc/ssh/{pattern}"
                for included in reader.glob(full):
                    sub = parse_sshd_config(reader, included, ev, depth + 1)
                    for k, v in (sub or {}).items():
                        values.setdefault(k, v)
            continue
        values.setdefault(key, value.lower())
    return values


def check_hardening(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    if not sshd_installed(reader):
        ev.append(Evidence(f"{SSHD_BINARY}, {SSHD_CONFIG}", "no SSH server is installed"))
        return Assessment(
            "ssh_hardening",
            HARDENING,
            Result.PASS,
            Severity.INFO,
            "Not applicable: no SSH server is installed, so there is no server configuration "
            "to harden.",
            tuple(ev),
        )
    cfg = parse_sshd_config(reader, SSHD_CONFIG, ev)
    if cfg is None:
        return Assessment(
            "ssh_hardening",
            HARDENING,
            Result.UNKNOWN,
            Severity.INFO,
            "An SSH server is installed but its configuration could not be read.",
            tuple(ev),
        )

    effective = {k: cfg.get(k, d) for k, d in DEFAULTS.items()}
    for key, value in effective.items():
        origin = "set" if key in cfg else "default (not set in the file)"
        ev.append(Evidence(SSHD_CONFIG, f"{key} = {value} ({origin})"))

    failures: list[str] = []
    warnings: list[str] = []
    if effective["permitrootlogin"] == "yes":
        failures.append("root login over SSH is allowed with a password (PermitRootLogin yes)")
    if effective["permitemptypasswords"] == "yes":
        failures.append("accounts with empty passwords may log in (PermitEmptyPasswords yes)")
    if effective["passwordauthentication"] == "yes":
        warnings.append("password authentication is enabled, which exposes SSH to guessing")

    note = " (static read of the config; Match blocks are not evaluated)"
    if failures:
        return Assessment(
            "ssh_hardening",
            HARDENING,
            Result.FAIL,
            Severity.HIGH,
            "; ".join([*failures, *warnings]).capitalize() + "." + note,
            tuple(ev),
            "Set `PermitRootLogin no`, `PermitEmptyPasswords no` and, once key login works, "
            "`PasswordAuthentication no` in /etc/ssh/sshd_config.d/, then `sudo systemctl "
            "reload ssh`. Keep a second session open while you test.",
        )
    if warnings:
        return Assessment(
            "ssh_hardening",
            HARDENING,
            Result.WARNING,
            Severity.MEDIUM,
            warnings[0].capitalize() + "." + note,
            tuple(ev),
            "Use key-based login and set `PasswordAuthentication no` in "
            "/etc/ssh/sshd_config.d/, then `sudo systemctl reload ssh`. Confirm key login "
            "works in a second session first.",
        )
    return Assessment(
        "ssh_hardening",
        HARDENING,
        Result.PASS,
        Severity.INFO,
        "Root login with a password, empty passwords and password authentication are all "
        "disabled" + note + ".",
        tuple(ev),
    )
