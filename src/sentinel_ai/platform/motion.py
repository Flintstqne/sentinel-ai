"""Decide whether to reduce motion.

Order of precedence: explicit config override, SENTINEL_REDUCED_MOTION, the Cinnamon
"enable-animations" setting, then default (motion allowed).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(cmd), capture_output=True, text=True, timeout=2, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(list(cmd), 1, "", "")


def desktop_animations_disabled(run: Runner = _run) -> bool | None:
    """True if Cinnamon animations are off, False if on, None if it can't be read."""
    proc = run(["gsettings", "get", "org.cinnamon.desktop.interface", "enable-animations"])
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip().lower()
    if value == "false":
        return True
    if value == "true":
        return False
    return None


def resolve_reduced_motion(
    config_value: bool | None,
    env: Mapping[str, str] | None = None,
    run: Runner = _run,
) -> bool:
    if config_value is not None:
        return config_value
    raw = (os.environ if env is None else env).get("SENTINEL_REDUCED_MOTION", "").lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return bool(desktop_animations_disabled(run))
