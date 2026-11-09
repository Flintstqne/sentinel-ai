# Wazuh setup for the SIEM-01 check

The `wazuh_alerts` check reads `/var/ossec/logs/alerts/alerts.json`. This guide installs
**only the Wazuh manager** on the same machine. It monitors this host by itself (as agent
`000`), so no separate agent is needed. The indexer and web dashboard are left out on
purpose: they want 8 GB or more of RAM, and Sentinel's dashboard already shows the alerts.

Checked against the official repository on 2026-11-09 (latest `wazuh-manager` 4.14.10, Linux
Mint 22.3 / Ubuntu noble). Every command needs root. Sentinel itself never runs any of this.

## 1. Add the Wazuh repository

```bash
sudo apt-get install -y gnupg apt-transport-https curl
curl -s https://packages.wazuh.com/key/GPG-KEY-WAZUH \
  | sudo gpg --no-default-keyring --keyring gnupg-ring:/usr/share/keyrings/wazuh.gpg --import
sudo chmod 644 /usr/share/keyrings/wazuh.gpg
echo "deb [signed-by=/usr/share/keyrings/wazuh.gpg] https://packages.wazuh.com/4.x/apt/ stable main" \
  | sudo tee /etc/apt/sources.list.d/wazuh.list
sudo apt-get update
```

## 2. Install the manager, but don't start it at boot

```bash
sudo apt-get install -y wazuh-manager
sudo systemctl disable wazuh-manager     # start it only when you want it (step 4)
sudo systemctl start wazuh-manager
systemctl is-active wazuh-manager        # expect: active
```

JSON alert output (`<jsonout_output>yes</jsonout_output>` in `/var/ossec/etc/ossec.conf`)
is on by default. Confirm it if `alerts.json` never appears:

```bash
sudo grep jsonout_output /var/ossec/etc/ossec.conf
```

## 3. Let your user read the alerts

The alerts directory is readable only by the `wazuh` group:

```bash
sudo usermod -aG wazuh "$USER"
```

Then **log out and back in** (group membership is read at login, and the duck autostarts
with your session). Check it worked:

```bash
id -nG | grep -w wazuh
ls -l /var/ossec/logs/alerts/alerts.json
```

## 4. Day to day

```bash
sudo systemctl start wazuh-manager   # before a demo or a work session
sudo systemctl stop wazuh-manager    # to give the RAM back
```

While the manager is stopped, `alerts.json` goes stale and SIEM-01 reports **Not assessed**
after 24 hours. That is intended: a dead feed is never a PASS.

## 5. Verify with Sentinel

```bash
cd ~/Developer/sentinel-ai
.venv/bin/python -m sentinel_ai --scan | grep -A6 SIEM-01
```

| You see | Meaning |
|---|---|
| `/var/ossec: does not exist` | Not installed |
| `not readable: Permission denied` | Step 3 not done, or you have not logged in again |
| `has not been written in over 24 hours` | The manager is stopped, or has had nothing to write |
| `PASS` with `last written ...` | Live feed, no alert at level 7 or above in 24 hours |
| `WARNING` / `FAIL` with `rule <id>` evidence | Real alerts; review them |

## 6. Producing real alerts for a demo

Cause real events rather than writing fake lines into the file. For example, enter a wrong
password for `sudo` a few times, then wait a minute and scan again. Look at what Wazuh
actually raised before relying on it in a demo, because the rule and its level are Wazuh's
decision:

```bash
sudo tail -n 5 /var/ossec/logs/alerts/alerts.json | python3 -m json.tool --json-lines \
  | grep -E '"(level|description|id)"'
```

## Removing it

```bash
sudo systemctl stop wazuh-manager
sudo apt-get purge -y wazuh-manager
sudo rm -rf /var/ossec
sudo rm /etc/apt/sources.list.d/wazuh.list /usr/share/keyrings/wazuh.gpg
sudo gpasswd -d "$USER" wazuh
sudo apt-get update
```
