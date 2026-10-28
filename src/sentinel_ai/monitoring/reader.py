"""The only door through which checks touch the system.

Checks receive a Reader instead of calling open()/subprocess directly. That keeps them
read-only by construction and lets tests feed them canned evidence, including the awkward
cases (missing files, permission errors, conflicting sources).
"""

from __future__ import annotations

import glob
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

COMMAND_TIMEOUT_S = 10
MAX_TAIL_BYTES = 5_000_000


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Reader(Protocol):
    def read_text(self, path: str) -> str:
        """Raises FileNotFoundError or PermissionError (both OSError) like open() would."""

    def read_tail(self, path: str, max_bytes: int = MAX_TAIL_BYTES) -> str: ...

    def exists(self, path: str) -> bool: ...

    def glob(self, pattern: str) -> list[str]: ...

    def mtime(self, path: str) -> datetime | None: ...

    def run(self, argv: Sequence[str]) -> CommandResult:
        """Never raises: a missing binary or a timeout is a non-zero CommandResult."""

    def now(self) -> datetime:
        """Timezone-aware local time."""


class LocalReader:
    """Reads this machine as the current, unprivileged user. Never writes anything."""

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def read_tail(self, path: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", errors="replace")

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def glob(self, pattern: str) -> list[str]:
        return sorted(glob.glob(pattern))

    def mtime(self, path: str) -> datetime | None:
        try:
            return datetime.fromtimestamp(os.stat(path).st_mtime).astimezone()
        except OSError:
            return None

    def run(self, argv: Sequence[str]) -> CommandResult:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv from our own checks, no shell
                list(argv),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"{argv[0]}: timed out")
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def now(self) -> datetime:
        return datetime.now().astimezone()
