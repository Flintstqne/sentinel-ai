from datetime import timedelta

import pytest

from fakes import NOW, FakeReader
from sentinel_ai.monitoring.checks import auth, firewall, ports, ssh, updates
from sentinel_ai.monitoring.model import Result, Severity
from sentinel_ai.monitoring.reader import CommandResult

# ---------------------------------------------------------------------------- firewall


def ufw_host(enabled="yes", service="active", policy="DROP", status=None, **kw) -> FakeReader:
    files = {"/etc/ufw/ufw.conf": f"# comment\nENABLED={enabled}\nLOGLEVEL=low\n"}
    if policy is not None:
        files["/etc/default/ufw"] = f'IPV6=yes\nDEFAULT_INPUT_POLICY="{policy}"\n'
    commands = {"systemctl is-active ufw": (0 if service == "active" else 3, service + "\n")}
    commands["ufw status"] = (0, status) if status else (1, "")
    return FakeReader(files, commands, **kw)


def test_firewall_conflicting_evidence_is_a_fail_not_a_pass() -> None:
    """The real case on the dev machine: service 'active' but ENABLED=no."""
    a = firewall.check(ufw_host(enabled="no", service="active"))
    assert (a.result, a.severity) == (Result.FAIL, Severity.HIGH)
    assert "not enforcing" in a.explanation and "active" in a.explanation
    assert a.remediation and "ufw enable" in a.remediation
    sources = {e.source: e.observation for e in a.evidence}
    assert sources["/etc/ufw/ufw.conf"] == "ENABLED=no"
    assert sources["systemctl is-active ufw"] == "active"


def test_firewall_pass_requires_config_service_and_policy_and_states_its_limit() -> None:
    a = firewall.check(ufw_host())
    assert a.result is Result.PASS and a.evidence
    assert "not inspected" in a.explanation  # live rules need root; say so


def test_firewall_pass_with_readable_live_status_drops_the_caveat() -> None:
    a = firewall.check(ufw_host(status="Status: active\n"))
    assert a.result is Result.PASS and "not inspected" not in a.explanation


@pytest.mark.parametrize(
    "kwargs, severity",
    [
        ({"service": "inactive"}, Severity.HIGH),
        ({"enabled": ""}, Severity.HIGH),
        ({"status": "Status: inactive\n"}, Severity.HIGH),
        ({"policy": "ACCEPT"}, Severity.MEDIUM),
    ],
)
def test_firewall_each_broken_part_fails(kwargs, severity) -> None:
    a = firewall.check(ufw_host(**kwargs))
    assert (a.result, a.severity) == (Result.FAIL, severity)


def test_firewall_missing_policy_file_is_only_a_warning() -> None:
    a = firewall.check(ufw_host(policy=None))
    assert a.result is Result.WARNING


def test_firewall_unreadable_config_is_unknown_never_pass() -> None:
    reader = FakeReader(unreadable={"/etc/ufw/ufw.conf"}, files={"/usr/sbin/ufw": ""})
    a = firewall.check(reader)
    assert a.result is Result.UNKNOWN
    assert any("permission denied" in e.observation for e in a.evidence)


def test_firewall_not_installed_but_nftables_active_cannot_be_judged() -> None:
    a = firewall.check(FakeReader(commands={"systemctl is-active nftables": (0, "active\n")}))
    assert a.result is Result.UNKNOWN and "nftables" in a.explanation


def test_firewall_nothing_detected_is_a_warning_with_caveat() -> None:
    reader = FakeReader(
        commands={
            "systemctl is-active nftables": (3, "inactive\n"),
            "systemctl is-active firewalld": (3, "inactive\n"),
        }
    )
    a = firewall.check(reader)
    assert a.result is Result.WARNING and "cannot be checked without root" in a.explanation


# --------------------------------------------------------------------------------- ssh

SSHD = {"/usr/sbin/sshd": "", "/etc/ssh/sshd_config": ""}


def ss_ssh(listening: bool) -> str:
    return "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n" if listening else "LISTEN 0 4096 127.0.0.1:631 *\n"


def sshd_host(ssh_state="active", socket="inactive", listening=True, ss_ok=True) -> FakeReader:
    commands = {
        "systemctl is-active ssh": (0, ssh_state + "\n"),
        "systemctl is-active ssh.socket": (0, socket + "\n"),
        "ss -tlnH": (0, ss_ssh(listening)) if ss_ok else (1, ""),
    }
    return FakeReader(dict(SSHD), commands)


def test_ssh_not_installed_is_a_pass_with_evidence() -> None:
    for fn in (ssh.check_exposure, ssh.check_hardening):
        a = fn(FakeReader())
        assert a.result is Result.PASS and a.evidence


def test_ssh_running_is_a_low_warning_naming_the_listener() -> None:
    a = ssh.check_exposure(sshd_host())
    assert (a.result, a.severity) == (Result.WARNING, Severity.LOW)
    assert "0.0.0.0:22" in a.explanation


def test_ssh_socket_activation_counts_as_running() -> None:
    a = ssh.check_exposure(sshd_host(ssh_state="inactive", socket="active", listening=False))
    assert a.result is Result.WARNING


def test_ssh_installed_but_stopped_passes_only_with_listener_evidence() -> None:
    a = ssh.check_exposure(sshd_host("inactive", "inactive", listening=False))
    assert a.result is Result.PASS
    assert any("none" in e.observation for e in a.evidence)
    b = ssh.check_exposure(sshd_host("inactive", "inactive", listening=False, ss_ok=False))
    assert b.result is Result.UNKNOWN  # cannot confirm nothing is listening


def hardening(config: str, extra: dict[str, str] | None = None) -> Result:
    files = {"/usr/sbin/sshd": "", "/etc/ssh/sshd_config": config, **(extra or {})}
    return ssh.check_hardening(FakeReader(files))


def test_ssh_hardening_matrix() -> None:
    assert hardening("PasswordAuthentication no\n").result is Result.PASS
    assert hardening("").result is Result.WARNING  # default is password auth ON
    assert hardening("PasswordAuthentication yes\n").result is Result.WARNING
    assert hardening("PasswordAuthentication no\nPermitRootLogin yes\n").result is Result.FAIL
    assert hardening("PasswordAuthentication no\nPermitEmptyPasswords yes\n").result is Result.FAIL
    assert (
        hardening("PasswordAuthentication no\nPermitRootLogin prohibit-password\n").result
        is Result.PASS
    )


def test_ssh_first_value_wins_and_comments_are_ignored() -> None:
    cfg = "# PasswordAuthentication yes\nPasswordAuthentication no\nPasswordAuthentication yes\n"
    assert hardening(cfg).result is Result.PASS
    assert hardening("PasswordAuthentication no # trailing comment\n").result is Result.PASS


def test_ssh_directives_after_match_are_conditional_and_ignored() -> None:
    cfg = "PasswordAuthentication no\nMatch User deploy\n    PermitRootLogin yes\n"
    assert hardening(cfg).result is Result.PASS


def test_ssh_include_is_followed_and_first_value_wins_across_files() -> None:
    cfg = "Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication yes\n"
    extra = {"/etc/ssh/sshd_config.d/50-hardening.conf": "PasswordAuthentication no\n"}
    assert hardening(cfg, extra).result is Result.PASS  # the included file came first


def test_ssh_self_including_config_does_not_recurse_forever() -> None:
    cfg = "Include /etc/ssh/sshd_config\nPasswordAuthentication no\n"
    assert hardening(cfg).result is Result.PASS


def test_ssh_unreadable_config_is_unknown() -> None:
    reader = FakeReader({"/usr/sbin/sshd": ""}, unreadable={"/etc/ssh/sshd_config"})
    assert ssh.check_hardening(reader).result is Result.UNKNOWN


def test_ssh_hardening_evidence_distinguishes_set_from_default() -> None:
    a = hardening("PasswordAuthentication no\n")
    notes = [e.observation for e in a.evidence]
    assert "passwordauthentication = no (set)" in notes
    assert any("permitrootlogin" in n and "default" in n for n in notes)


# -------------------------------------------------------------------------------- auth


def stamp(delta: timedelta, fmt: str = "iso") -> str:
    when = NOW - delta
    if fmt == "iso":
        return when.isoformat(timespec="microseconds")
    return when.strftime("%b %e %H:%M:%S")


def log_with(count: int, age: timedelta = timedelta(hours=1), fmt: str = "iso") -> str:
    lines = []
    for i in range(count):
        t = stamp(age, fmt)
        lines.append(f"{t} host sshd[{i}]: Failed password for invalid user bob from 1.2.3.4")
    lines.append(f"{stamp(timedelta(minutes=1), fmt)} host CRON[9]: session opened for root")
    return "\n".join(lines) + "\n"


def auth_host(text: str, **kw) -> FakeReader:
    return FakeReader({"/var/log/auth.log": text}, **kw)


@pytest.mark.parametrize(
    "count, result",
    [
        (0, Result.PASS),
        (4, Result.PASS),
        (5, Result.WARNING),
        (99, Result.WARNING),
        (100, Result.FAIL),
    ],
)
def test_auth_thresholds(count, result) -> None:
    assert auth.check(auth_host(log_with(count))).result is result


def test_auth_ignores_lines_older_than_24_hours() -> None:
    a = auth.check(auth_host(log_with(500, age=timedelta(hours=25))))
    assert a.result is Result.PASS and "0 failed" in a.explanation


def test_auth_parses_classic_syslog_timestamps_too() -> None:
    assert auth.check(auth_host(log_with(7, fmt="syslog"))).result is Result.WARNING


def test_auth_reports_which_programs_the_failures_came_from() -> None:
    text = (
        log_with(3)
        + f"{stamp(timedelta(hours=1))} host sudo: pam_unix(sudo:auth): authentication failure\n"
    )
    a = auth.check(auth_host(text))
    by_program = next(e.observation for e in a.evidence if e.observation.startswith("by program"))
    assert "sshd: 3" in by_program and "sudo: 1" in by_program


def test_auth_falls_back_to_the_journal_when_the_log_is_unreadable() -> None:
    journal = log_with(8, fmt="syslog")
    reader = FakeReader(
        unreadable={"/var/log/auth.log"},
        commands={
            f"journalctl --no-pager -q --since 24 hours ago -g {auth.PATTERN.pattern}": (0, journal)
        },
    )
    a = auth.check(reader)
    assert a.result is Result.WARNING
    assert any("journalctl" in e.source for e in a.evidence)


def test_auth_with_no_readable_source_is_unknown_never_pass() -> None:
    a = auth.check(FakeReader(unreadable={"/var/log/auth.log"}))
    assert a.result is Result.UNKNOWN and "adm" in a.explanation


def test_auth_log_time_parsing() -> None:
    assert auth.parse_log_time("2026-10-27T11:00:00.123456-04:00 h p: x", NOW) == NOW.replace(
        hour=11, microsecond=0
    )
    assert auth.parse_log_time("2026-10-27T15:00:00Z h p: x", NOW) == NOW.replace(hour=11, minute=0)
    assert auth.parse_log_time("Oct 27 11:00:00 h p: x", NOW) == NOW.replace(hour=11)
    assert auth.parse_log_time("garbage", NOW) is None
    # a December line seen in January belongs to last year
    jan = NOW.replace(year=2027, month=1, day=2)
    assert auth.parse_log_time("Dec 31 23:00:00 h p: x", jan).year == 2026


# ----------------------------------------------------------------------------- updates

UPDATES = "/var/lib/update-notifier/updates-available"


def updates_host(text: str, age: timedelta = timedelta(hours=4)) -> FakeReader:
    return FakeReader({UPDATES: text}, mtimes={UPDATES: NOW - age})


def test_updates_zero_in_the_real_file_format_is_a_pass() -> None:
    real = "\nExpanded Security Maintenance for Applications is not enabled.\n\n0 updates can be applied immediately.\n"
    a = updates.check(updates_host(real))
    assert a.result is Result.PASS and "security updates: 0" in a.evidence[-1].observation


def test_updates_security_updates_warn() -> None:
    text = (
        "5 updates can be applied immediately.\n2 of these updates are standard security updates.\n"
    )
    a = updates.check(updates_host(text))
    assert (a.result, a.severity) == (Result.WARNING, Severity.MEDIUM)
    assert "2 security updates" in a.explanation


def test_updates_singular_wording() -> None:
    text = (
        "1 update can be applied immediately.\n1 of these updates is a standard security update.\n"
    )
    assert "1 security update pending" in updates.check(updates_host(text)).explanation


def test_updates_stale_missing_or_unstated_data_is_unknown_never_pass() -> None:
    fresh = "0 updates can be applied immediately.\n"
    assert updates.check(updates_host(fresh, age=timedelta(days=8))).result is Result.UNKNOWN
    assert updates.check(FakeReader()).result is Result.UNKNOWN
    assert updates.check(updates_host("nothing useful here\n")).result is Result.UNKNOWN
    no_mtime = FakeReader({UPDATES: fresh})
    assert updates.check(no_mtime).result is Result.UNKNOWN


# -------------------------------------------------------------------------------- ports

REAL_SS = """\
udp UNCONN 0      0            0.0.0.0:5353  0.0.0.0:*
udp UNCONN 0      0         127.0.0.54:53    0.0.0.0:*
udp UNCONN 0      0               [::]:5353     [::]:*
tcp LISTEN 0      10           0.0.0.0:7070  0.0.0.0:*
tcp LISTEN 0      4096       127.0.0.1:631   0.0.0.0:*
tcp LISTEN 0      4096   127.0.0.53%lo:53    0.0.0.0:*
tcp LISTEN 0      10              [::]:7070     [::]:*
tcp LISTEN 0      5              [::1]:902      [::]:*
"""


def ports_host(output: str, rc: int = 0) -> FakeReader:
    return FakeReader(commands={"ss -tulnHp": (rc, output)})


def test_ports_real_output_warns_on_the_one_exposed_tcp_port() -> None:
    a = ports.check(ports_host(REAL_SS))
    assert (a.result, a.severity) == (Result.WARNING, Severity.MEDIUM)
    assert "TCP port 7070 accepts" in a.explanation
    seen = [e.observation for e in a.evidence]
    assert "exposed: tcp [::]:7070" in seen and "exposed: tcp 0.0.0.0:7070" in seen
    assert "UDP sockets reachable on ports: 5353" in seen  # de-duplicated across IPv4/IPv6


def test_ports_loopback_only_passes() -> None:
    a = ports.check(
        ports_host("tcp LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\ntcp LISTEN 0 5 [::1]:902 [::]:*\n")
    )
    assert a.result is Result.PASS and a.evidence


def test_ports_exposed_database_is_a_high_severity_fail() -> None:
    a = ports.check(ports_host("tcp LISTEN 0 200 0.0.0.0:5432 0.0.0.0:*\n"))
    assert (a.result, a.severity) == (Result.FAIL, Severity.HIGH)
    assert "PostgreSQL" in a.explanation


def test_ports_lan_address_counts_as_exposed() -> None:
    a = ports.check(ports_host("tcp LISTEN 0 5 192.0.2.10:8080 0.0.0.0:*\n"))
    assert a.result is Result.WARNING


def test_ports_command_failure_or_empty_output_is_unknown_never_pass() -> None:
    assert ports.check(ports_host("", rc=1)).result is Result.UNKNOWN
    assert ports.check(ports_host("")).result is Result.UNKNOWN
    assert ports.check(FakeReader()).result is Result.UNKNOWN


def test_ports_parse_ss_handles_each_address_shape() -> None:
    lst = {
        (x.address, x.port): x
        for x in ports.parse_ss(
            "tcp LISTEN 0 1 *:80 *:*\n"
            'tcp LISTEN 0 1 [::1]:631 [::]:* users:(("cupsd",12,6))\n'
            "tcp LISTEN 0 1 127.0.0.53%lo:53 0.0.0.0:*\n"
            "junk line\n"
        )
    }
    assert lst[("*", 80)].exposed
    assert not lst[("::1", 631)].exposed and lst[("::1", 631)].process == "cupsd"
    assert not lst[("127.0.0.53%lo", 53)].exposed


def test_listener_formats_ipv6_with_brackets() -> None:
    assert str(ports.Listener("tcp", "::", 7070, None)) == "tcp [::]:7070"
    assert CommandResult(0, "", "").returncode == 0
