"""Loads the bundled fonts and builds QFonts from the type roles in theme.TYPE.

If a bundled font fails to load the role falls back to a system family, so the UI always
renders; it just looks less distinctive.
"""

from __future__ import annotations

import logging
from importlib import resources

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from sentinel_ai.ui.theme import (
    FALLBACK_MONO,
    FALLBACK_UI,
    FONT_DISPLAY,
    FONT_MONO,
    FONT_UI,
    TYPE,
)

log = logging.getLogger(__name__)

_BUNDLED = (
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Medium.ttf",
    "IBMPlexSans-SemiBold.ttf",
    "IBMPlexMono-Regular.ttf",
    "IBMPlexMono-Medium.ttf",
    "PixelifySans.ttf",
)
_loaded: set[str] = set()


def load_fonts() -> set[str]:
    """Register the bundled fonts with Qt (idempotent). Returns the families now available."""
    if _loaded:
        return _loaded
    folder = resources.files("sentinel_ai") / "assets" / "fonts"
    for name in _BUNDLED:
        try:
            with resources.as_file(folder / name) as path:
                font_id = QFontDatabase.addApplicationFont(str(path))
        except (FileNotFoundError, OSError):
            log.warning("bundled font missing", extra={"font": name})
            continue
        if font_id < 0:
            log.warning("bundled font failed to load", extra={"font": name})
            continue
        _loaded.update(QFontDatabase.applicationFontFamilies(font_id))
    return _loaded


def _families(key: str) -> list[str]:
    wanted = {"ui": FONT_UI, "mono": FONT_MONO, "display": FONT_DISPLAY}[key]
    system = FALLBACK_MONO if key == "mono" else FALLBACK_UI
    primary = [wanted] if wanted in _loaded else []
    return [*primary, *[f.strip() for f in system.split(",")]]


def apply_app_font(app: QApplication) -> None:
    """Make the body font the application default (so menus and tooltips match too)."""
    load_fonts()
    app.setFont(make_font("body"))


def make_font(role: str) -> QFont:
    key, px, weight, spacing = TYPE[role]
    font = QFont()
    font.setFamilies(_families(key))
    font.setPixelSize(px)
    font.setWeight(QFont.Weight(weight))
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    return font
