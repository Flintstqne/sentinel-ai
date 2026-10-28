"""Pixel-art status icons, drawn on the same grid language as the duck.

Each result has its own *shape* as well as its own colour (check, cross, warning triangle,
question mark, bolt), so a status never depends on colour alone.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from sentinel_ai.companion.render import render_frame
from sentinel_ai.companion.state_machine import Tone
from sentinel_ai.monitoring.model import Result
from sentinel_ai.ui.theme import PALETTE

_GLYPHS: dict[str, tuple[str, ...]] = {
    "check": (
        ".........",
        ".......##",
        "......###",
        "##...###.",
        "###.###..",
        ".#####...",
        "..###....",
        "...#.....",
        ".........",
    ),
    "cross": (
        ".........",
        ".##...##.",
        ".###.###.",
        "..#####..",
        "...###...",
        "..#####..",
        ".###.###.",
        ".##...##.",
        ".........",
    ),
    "warning": (
        "....#....",
        "...###...",
        "...###...",
        "..##.##..",
        "..##.##..",
        ".###.###.",
        ".#######.",
        ".###.###.",
        "#########",
    ),
    "question": (
        "..#####..",
        ".##...##.",
        ".##...##.",
        ".....##..",
        "....##...",
        "....#....",
        ".........",
        "....#....",
        ".........",
    ),
    "bolt": (
        ".....###.",
        "....###..",
        "...###...",
        "..######.",
        "....###..",
        "...###...",
        "..###....",
        "..##.....",
        ".#.......",
    ),
}

_BY_RESULT = {
    Result.PASS: ("check", PALETTE.good),
    Result.WARNING: ("warning", PALETTE.warning),
    Result.FAIL: ("cross", PALETTE.danger),
    Result.UNKNOWN: ("question", PALETTE.neutral),
    Result.ERROR: ("bolt", PALETTE.neutral),
}


@lru_cache(maxsize=64)
def status_pixmap(result: Result, scale: int = 2) -> QPixmap:
    name, colour = _BY_RESULT[result]
    glyph = _GLYPHS[name]
    image = QImage(9 * scale, 9 * scale, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for y, row in enumerate(glyph):
        for x, cell in enumerate(row):
            if cell == "#":
                painter.fillRect(x * scale, y * scale, scale, scale, QColor(colour))
    painter.end()
    return QPixmap.fromImage(image)


@lru_cache(maxsize=16)
def duck_head(scale: int = 2) -> QPixmap:
    """The front-facing duck's head, for the wordmark."""
    return render_frame("base", Tone.NORMAL, scale).copy(3 * scale, 0, 18 * scale, 15 * scale)


@lru_cache(maxsize=16)
def duck_full(scale: int = 4) -> QPixmap:
    return render_frame("base", Tone.NORMAL, scale)


def clear_pixmap_caches() -> None:
    """Drop every cached QPixmap. They must be released while the QApplication still exists:
    a pixmap destroyed after it (during interpreter shutdown) crashes the process."""
    for cached in (render_frame, status_pixmap, duck_head, duck_full):
        cached.cache_clear()
