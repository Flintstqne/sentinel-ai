# Security checks (Phase 3)

Six read-only checks run as the logged-in user. Nothing is changed on the system, no
privilege is requested, and no remediation is ever executed: advice is text for a human.

## Result semantics

| Result | Meaning |
|---|---|
| PASS | The collected evidence confirms the control is satisfied. **A PASS cannot be constructed without at least one piece of evidence** (enforced in `Assessment.__post_init__`). |
| FAIL | The evidence confirms the control is not satisfied. |
| WARNING | The evidence shows a weakness or a condition worth a human's review. |
| UNKNOWN | The evidence needed to decide could not be collected (missing file, permission denied, stale data). Never reported as PASS. |
| ERROR | The check itself crashed. Says nothing about the system. Never a PASS, never a finding. |

FAIL and WARNING open **findings**. UNKNOWN and ERROR do not (nothing was confirmed), and
they never close an existing finding either.

Evidence holds only observations (file or command, and what was seen). Interpretation
(explanation, severity, remediation) lives on the assessment, so each can be reasoned about
separately and the facts beneath a conclusion are never edited by it.

## Controls

The framework references are an **illustrative mapping**, not a compliance claim.

| Control | Check | What it reads | Notes |
|---|---|---|---|
| FW-01 (NIST SP 800-53 SC-7) | Host firewall enabled and denying inbound by default | `/etc/ufw/ufw.conf`, `systemctl is-active ufw`, `/etc/default/ufw`, `ufw status` (needs root, usually unreadable) | The configuration is authoritative for "enabled". A service reporting `active` with `ENABLED=no` is a **FAIL**. PASS needs config enabled, service active and a deny-by-default inbound policy, and says that live rules were not inspected when root is unavailable. UFW absent with nftables or firewalld active is UNKNOWN (their rules need root). |
| SSH-01 (CM-7, AC-17) | SSH server exposure | sshd files, `systemctl is-active ssh ssh.socket`, `ss -tlnH` | Not installed is a PASS. Running is a LOW warning to confirm it is intended. Socket activation counts as running. |
| SSH-02 (AC-17, IA-5) | SSH server hardening | `/etc/ssh/sshd_config` and `Include`d files | Static parse, first value wins, stops at `Match`, include depth capped. Not `sshd -T` (root). `PermitRootLogin yes` and `PermitEmptyPasswords yes` are FAIL; password authentication (explicit or the OpenSSH default) is a WARNING. Not applicable (no sshd) is reported as PASS with that explanation. |
| AUTH-01 (AC-7, AU-6) | Failed authentication attempts | `/var/log/auth.log` (readable via the `adm` group), else `journalctl` | Last 24 hours. Below 5 PASS, 5 to 99 WARNING, 100 or more FAIL. Evidence includes a per-program breakdown. Both ISO and classic syslog timestamps are parsed. |
| PATCH-01 (SI-2) | Pending security updates | `/var/lib/update-notifier/updates-available` | The notifier omits the security line when there are none, so a stated total with no security line means zero. Data older than 7 days is UNKNOWN, never trusted. |
| NET-01 (CM-7, SC-7) | Exposed network services | `ss -tulnHp` | Wildcard or non-loopback TCP listeners warn; a known-dangerous service (databases, RDP, VNC, SMB, Telnet...) is a FAIL. UDP is listed as evidence but not judged (mDNS is routinely open). |

## Severity rubric

HIGH means a confirmed failure of a core control (firewall not enforcing, root or empty-
password SSH, brute-force volume, a dangerous service exposed). MEDIUM is a confirmed
weakness or exposure that needs a decision. LOW is something to confirm is intended. Only a
**FAIL at HIGH or above** turns the companion red; a HIGH *warning* does not.

## Finding lifecycle (SQLite)

Two tables: `assessments` keeps every control's result for every scan, passes included (the
audit trail), and `findings` holds de-duplicated issues with a status.

* FAIL/WARNING opens a finding, or refreshes the open one (`last_seen_at`, evidence).
* PASS resolves an open or acknowledged finding. Evidence beats acknowledgement.
* UNKNOWN/ERROR leave findings untouched.
* A severity increase is reported as an escalation and reopens an acknowledged finding.
* A recurrence after resolution is a new finding with a new ID.
* The host is identified by hostname plus a short hash of the machine-id; the raw
  machine-id is never stored or logged.

## How a scan drives the companion

Scans run on a worker thread (scheduled every `scan_interval_minutes`, default 60, and on
demand from the menu or `Run scan`). SCANNING plays while it runs. Afterwards the duck
reacts to what **changed**, not to what merely still exists:

| Scan outcome | Companion | Notification |
|---|---|---|
| New or escalated FAIL at HIGH+ | ALERT, red | yes |
| New or escalated MEDIUM issue | ALERT, amber | yes |
| New LOW issue | INSIGHT | yes |
| Nothing new, findings still open | settle to idle | no |
| Everything passes, nothing open | HAPPY | no |
| Only UNKNOWN/ERROR | settle to idle | no |

Notifications are capped at three per scan plus a summary line, are never sent while
monitoring is paused, and can be disabled with `"notifications": false`.

## Verified on this machine (Cinnamon 6.6.9)

| Behaviour | Result |
|---|---|
| Automatic first scan after launch | ran at about 10 s; firewall FAIL, ports WARNING, four PASS (matching hand-checked ground truth) |
| Companion reaction | red ALERT with 2 notifications |
| Desktop notifications | delivered on the session bus: "High: ..." with the error icon and "Medium: ..." with the warning icon, with the explanation text |
| Status panel and dashboard | show the real counts and the full report with evidence |
| Persistence across processes | a second scan (headless `--scan`) updated `last_seen_at`, left `detected_at` alone and created no duplicates |
| Headless `sentinel-ai --scan` | exit 0, report on stdout, nothing on stderr |
| Raw machine-id in the database or log | none |
| Startup stderr | clean (a startup traceback from the earlier walking work was found and fixed) |

## Not verified or known limits

* **Run scan from the menu, pause and resume with a live scan, and notification clicks** were
  not exercised on the live desktop; they are covered by automated tests.
* Live firewall rules, `sshd -T`, and the owning process of other users' sockets all need
  root; the app reports that limit in the evidence instead of guessing.
* The duck now starts bottom-left so the bottom-right notification bubble does not cover it;
  once you drag it somewhere its saved position is used.
* Remediation text is advice for a human and was not tested against a live system change.
