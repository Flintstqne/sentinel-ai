# Phase 1: Repository and environment assessment

Date: 2026-10-27. Host: Linux Mint, Cinnamon 6.6.9.

## Existing project

The handoff describes an earlier PySide6 MVP (firewall and SSH checks, JSON export, tests).
**That repository was not found on this machine.** A filesystem search for `sentinel`
(and for any PySide6 code outside unrelated projects) returned nothing, so this project was
scaffolded fresh, as the handoff instructs. Nothing was reused. If the original repo turns
up, its check logic can be ported into Phase 3 behind the interfaces described in
`ARCHITECTURE.md`.

## Environment

| Item | Finding |
|---|---|
| `XDG_SESSION_TYPE` | `x11` (X11 is real here, not assumed) |
| `XDG_CURRENT_DESKTOP` | `X-Cinnamon` |
| Python | 3.12.3 (project requires 3.11+) |
| PySide6 | not installed system-wide; installed in the project venv |
| X11 tools | `xprop`, `wmctrl`, `xdotool`, `xwininfo`, `Xvfb` available |
| Privilege | no passwordless sudo; the app must work unprivileged |

## What an unprivileged process can actually see (drives Phase 3 design)

| Check | Readable without root? | Notes |
|---|---|---|
| UFW enabled in config | yes: `/etc/ufw/ufw.conf`, `/etc/default/ufw` are world-readable | `ENABLED=no` on the development host |
| UFW service state | yes: `systemctl is-active ufw` | reports `active` on the development host |
| UFW live rules | **no**: `ufw status` needs root; `/etc/ufw/user.rules` is root-only | must be reported as UNKNOWN, never PASS |
| SSH service | yes: `systemctl is-active ssh` | inactive, and `/etc/ssh/sshd_config` is absent (server not installed) |
| Listening ports | yes: `ss -tulnH` | |
| Auth failures | yes, because the user is in the `adm` group: `/var/log/auth.log` is `syslog:adm 640` | access depends on group membership |
| Pending updates | partly: `/var/lib/update-notifier/updates-available` is world-readable | |

### The firewall finding worth demonstrating

On the development host `systemctl is-active ufw` says **active** while `/etc/ufw/ufw.conf` says
**`ENABLED=no`**. A naive check that trusts the service state reports a passing firewall
control when the firewall is not enabled. Phase 3 must treat the config as authoritative
for "enabled", and report live-rule state as UNKNOWN when it cannot be read. This is the
core of the firewall control demonstration: *evidence conflicts are reported as such, not smoothed
over.*

## Decisions

* Qt-free core logic (state machine, sprite data, config, X11 adapter) so it is unit-tested
  without a display; Qt tests run on the `offscreen` platform and never touch the desktop.
* XDG paths for config (`~/.config/sentinel-ai`) and logs (`~/.local/state/sentinel-ai`).
* No privileged background service. Read-only collection as the logged-in user.
