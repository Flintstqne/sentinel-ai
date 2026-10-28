"""User preferences, persisted as JSON under the XDG config directory."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIN_SCALE = 2
MAX_SCALE = 8
MAX_SCAN_INTERVAL_MINUTES = 24 * 60


def config_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sentinel-ai"


def data_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "sentinel-ai"


def state_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "sentinel-ai"


@dataclass
class Config:
    companion_x: int | None = None
    companion_y: int | None = None
    scale: int = 3  # the 24-cell sprite at 3 px is about the size the 20-cell one had at 4
    # None means "follow the desktop setting"; True/False is an explicit override.
    reduced_motion: bool | None = None
    monitoring_paused: bool = False
    roam: bool = True  # wander along the desktop while idle
    scan_interval_minutes: int = 60  # 0 = only scan when asked
    notifications: bool = True

    @classmethod
    def default_path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or cls.default_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            corrupt = path.with_suffix(".json.corrupt")
            log.warning(
                "config unreadable, using defaults", extra={"path": str(path), "err": str(exc)}
            )
            with contextlib.suppress(OSError):
                path.replace(corrupt)
            return cls()
        if not isinstance(raw, dict):
            log.warning("config is not an object, using defaults", extra={"path": str(path)})
            return cls()
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> Config:
        cfg = cls()
        for key in ("companion_x", "companion_y"):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(cfg, key, value)
        scale = raw.get("scale")
        if isinstance(scale, int) and not isinstance(scale, bool):
            cfg.scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        motion = raw.get("reduced_motion")
        if motion is None or isinstance(motion, bool):
            cfg.reduced_motion = motion
        paused = raw.get("monitoring_paused")
        if isinstance(paused, bool):
            cfg.monitoring_paused = paused
        roam = raw.get("roam")
        if isinstance(roam, bool):
            cfg.roam = roam
        interval = raw.get("scan_interval_minutes")
        if isinstance(interval, int) and not isinstance(interval, bool):
            cfg.scan_interval_minutes = max(0, min(MAX_SCAN_INTERVAL_MINUTES, interval))
        notifications = raw.get("notifications")
        if isinstance(notifications, bool):
            cfg.notifications = notifications
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or self.default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def field_names() -> tuple[str, ...]:
        return tuple(f.name for f in fields(Config))
