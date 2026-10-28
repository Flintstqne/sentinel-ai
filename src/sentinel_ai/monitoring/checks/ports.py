"""NET-01: which network services accept connections from other machines?"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity
from sentinel_ai.monitoring.reader import Reader

CONTROL = Control(
    "NET-01",
    "Only necessary network services are exposed",
    "NIST SP 800-53 Rev. 5 CM-7 (Least Functionality), SC-7 (Boundary Protection), illustrative",
)
CHECK_ID = "listening_ports"

# Services that should essentially never face a network unprotected.
RISKY_TCP = {
    21: "FTP",
    23: "Telnet",
    111: "RPC portmapper",
    139: "NetBIOS",
    445: "SMB",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    9200: "Elasticsearch",
    11211: "Memcached",
    27017: "MongoDB",
}
_WILDCARDS = {"0.0.0.0", "*", "::", "[::]"}
_PROC = re.compile(r'\(\("([^"]+)"')


@dataclass(frozen=True)
class Listener:
    proto: str
    address: str
    port: int
    process: str | None

    @property
    def exposed(self) -> bool:
        """Reachable from other machines: a wildcard bind or a non-loopback address."""
        host = self.address.split("%", 1)[0]
        if host in _WILDCARDS:
            return True
        return not (host.startswith("127.") or host in ("::1", "[::1]"))

    def __str__(self) -> str:
        who = f" ({self.process})" if self.process else ""
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.proto} {host}:{self.port}{who}"


def parse_ss(output: str) -> list[Listener]:
    listeners = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("tcp", "udp"):
            continue
        addr, _, port = parts[4].rpartition(":")
        if not port.isdigit():
            continue
        proc = _PROC.search(line)
        listeners.append(
            Listener(
                parts[0],
                addr.strip("[]") if addr != "[::]" else "::",
                int(port),
                proc.group(1) if proc else None,
            )
        )
    return listeners


def check(reader: Reader) -> Assessment:
    ev: list[Evidence] = []
    proc = reader.run(["ss", "-tulnHp"])
    if proc.returncode != 0 or not proc.stdout.strip():
        ev.append(Evidence("ss -tulnHp", f"unavailable or empty: {proc.stderr.strip()[:120]}"))
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.UNKNOWN,
            Severity.INFO,
            "Listening sockets could not be read, so network exposure cannot be assessed.",
            tuple(ev),
        )
    listeners = parse_ss(proc.stdout)
    tcp = [x for x in listeners if x.proto == "tcp"]
    udp = [x for x in listeners if x.proto == "udp"]
    tcp_exposed = sorted({(x.port, str(x)) for x in tcp if x.exposed})
    udp_exposed = [x for x in udp if x.exposed]

    ev.append(
        Evidence(
            "ss -tulnHp",
            f"{len(tcp)} TCP listeners ({len(tcp_exposed)} reachable from other machines), "
            f"{len(udp)} UDP sockets ({len(udp_exposed)} reachable)",
        )
    )
    for _, text in tcp_exposed:
        ev.append(Evidence("ss -tulnHp", f"exposed: {text}"))
    if udp_exposed:
        ports = ", ".join(str(n) for n in sorted({x.port for x in udp_exposed}))
        ev.append(Evidence("ss -tulnHp", f"UDP sockets reachable on ports: {ports}"))
    unattributed = [x for x in tcp if x.exposed and not x.process]
    if unattributed:
        ev.append(
            Evidence(
                "ss -tulnHp",
                "owning process not visible for some listeners (needs root for other users' "
                "sockets)",
            )
        )

    risky = [(p, RISKY_TCP[p]) for p, _ in tcp_exposed if p in RISKY_TCP]
    if risky:
        names = ", ".join(f"{name} on port {port}" for port, name in risky)
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.FAIL,
            Severity.HIGH,
            f"Services that should not face the network are reachable from other machines: "
            f"{names}.",
            tuple(ev),
            "Bind them to 127.0.0.1 in their configuration, or block them with the firewall.",
        )
    if tcp_exposed:
        numbers = sorted({p for p, _ in tcp_exposed})
        ports = ", ".join(str(p) for p in numbers)
        subject = f"TCP ports {ports} accept" if len(numbers) > 1 else f"TCP port {ports} accepts"
        return Assessment(
            CHECK_ID,
            CONTROL,
            Result.WARNING,
            Severity.MEDIUM,
            f"{subject} connections from other machines. If that is not intended, bind the "
            "service to localhost or block it at the firewall. (UDP sockets are listed in the "
            "evidence but not "
            "judged: discovery protocols like mDNS are routinely open.)",
            tuple(ev),
            "Identify the service (`sudo ss -tulnp`), then either bind it to 127.0.0.1 or "
            "restrict it with the firewall.",
        )
    return Assessment(
        CHECK_ID,
        CONTROL,
        Result.PASS,
        Severity.INFO,
        "No TCP service accepts connections from other machines; every listener is bound to "
        "loopback.",
        tuple(ev),
    )
