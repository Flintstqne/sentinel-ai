"""Turns sprite grids into scaled pixmaps. Integer scaling only, so pixels stay crisp."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

from sentinel_ai.companion.sprites import HEIGHT, WIDTH, grid
from sentinel_ai.companion.state_machine import Tone
from sentinel_ai.ui.theme import SPRITE_COLORS

# Padding leaves room for animation offsets. Offsets only ever move the sprite sideways or UP
# (never below the baseline), so there is no padding under the feet: the window's bottom
# edge is the ground. That lets the window sit flush on the panel without the window manager
# pushing it up.
PAD_X_CELLS = 1
PAD_TOP_CELLS = 1


def canvas_size(scale: int) -> tuple[int, int]:
    return (WIDTH + 2 * PAD_X_CELLS) * scale, (HEIGHT + PAD_TOP_CELLS) * scale


@lru_cache(maxsize=512)
def render_frame(name: str, tone: Tone, scale: int, flip: bool = False) -> QPixmap:
    image = QImage(WIDTH * scale, HEIGHT * scale, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for y, row in enumerate(grid(name, tone, flip)):
        for x, cell in enumerate(row):
            colour = SPRITE_COLORS.get(cell)
            if colour is not None:
                painter.fillRect(x * scale, y * scale, scale, scale, QColor(colour))
    painter.end()
    return QPixmap.fromImage(image)


def tray_icon(size: int = 32) -> QIcon:
    """The front-facing duck's head, as a crisp icon for the system tray."""
    pixmap = render_frame("base", Tone.NORMAL, 1)
    head = pixmap.copy(3, 0, 18, 15)  # antenna, head, goggles and bill
    scaled = head.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    return QIcon(scaled)
