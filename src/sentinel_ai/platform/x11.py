"""X11 desktop-layer adapter for the companion window.

Goal: keep the companion above the desktop background (Nemo's desktop window) but below
normal application windows, off the taskbar and visible on every workspace.

Approach (chosen for reliability over cleverness):
1. Qt already requests _NET_WM_STATE_BELOW via Qt.WindowStaysOnBottomHint.
2. After the window is mapped we ask the window manager, through wmctrl, to add
   below / skip_taskbar / skip_pager / sticky. These are standard EWMH states that
   Muffin (Cinnamon) honours; no private WM calls and no _NET_WM_WINDOW_TYPE_DESKTOP,
   which would hand the window to the desktop layer and risks losing clicks to Nemo.
3. We then read the states back with xprop and report what the WM actually applied.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

WANTED_STATES = frozenset(
    {"_NET_WM_STATE_BELOW", "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_STICKY"}
)
# wmctrl -b takes at most TWO properties per call; extra ones are silently ignored.
_WMCTRL_ADD = ("add,below,skip_taskbar", "add,skip_pager,sticky")


def session_type(env: Mapping[str, str] | None = None) -> str:
    """'x11', 'wayland' or 'unknown', from XDG_SESSION_TYPE."""
    value = (os.environ if env is None else env).get("XDG_SESSION_TYPE", "").lower()
    return value if value in {"x11", "wayland"} else "unknown"


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(cmd), capture_output=True, text=True, timeout=3, check=False
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(cmd), 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(cmd), 124, "", f"{cmd[0]}: timed out")


def parse_net_wm_state(xprop_output: str) -> frozenset[str]:
    match = re.search(r"_NET_WM_STATE\(ATOM\)\s*=\s*(.*)", xprop_output)
    if not match:
        return frozenset()
    return frozenset(s.strip() for s in match.group(1).split(",") if s.strip())


@dataclass(frozen=True)
class PlacementResult:
    ok: bool  # True only if the window manager reports every state in WANTED_STATES
    method: str
    states: frozenset[str]
    message: str


def read_placement(window_id: int, run: Runner = _run) -> PlacementResult:
    proc = run(["xprop", "-id", hex(window_id), "_NET_WM_STATE"])
    if proc.returncode != 0:
        return PlacementResult(False, "xprop", frozenset(), proc.stderr.strip() or "xprop failed")
    states = parse_net_wm_state(proc.stdout)
    missing = WANTED_STATES - states
    message = "placement verified" if not missing else f"missing: {', '.join(sorted(missing))}"
    return PlacementResult(not missing, "xprop", states, message)


def request_desktop_layer(window_id: int, run: Runner = _run) -> bool:
    """Ask the WM for the desktop-layer states. True if wmctrl accepted the request.

    The WM applies state changes asynchronously, so read them back later with
    read_placement() instead of immediately.
    """
    ok = True
    for spec in _WMCTRL_ADD:
        proc = run(["wmctrl", "-i", "-r", hex(window_id), "-b", spec])
        if proc.returncode != 0:
            ok = False
            log.warning(
                "wmctrl unavailable or failed; relying on Qt window flags",
                extra={"rc": proc.returncode, "stderr": proc.stderr.strip()},
            )
            break
    return ok


def apply_desktop_layer(window_id: int, run: Runner = _run) -> PlacementResult:
    """Request the states and read them straight back (use only where the WM is synchronous)."""
    requested = request_desktop_layer(window_id, run)
    result = read_placement(window_id, run)
    method = "wmctrl+xprop" if requested else "qt-flags"
    return PlacementResult(result.ok, method, result.states, result.message)
