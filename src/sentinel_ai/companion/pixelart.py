"""A tiny pixel-art toolkit: shaded ellipses with consistent outlines. Pure Python.

Hand-placing every pixel of a rounded form gives lumpy curves. Rasterising an ellipse and
shading it from a fixed light direction gives clean, consistent curves; details are then
placed by hand on top. Everything is deterministic, so the output is the same on every run.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

Cell = tuple[int, int]  # (x, y)

# Light from the upper left and slightly in front, as a unit vector (x, y, z).
_LIGHT = (-0.45, -0.65, 0.6)
_NORM = math.sqrt(sum(c * c for c in _LIGHT))
LIGHT = tuple(c / _NORM for c in _LIGHT)

# Thresholds on the lit amount (-1..1) separating shadow, base, light and highlight.
_BASE, _LIGHTER, _HIGHLIGHT = 0.22, 0.80, 0.965


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._cells = [["."] * width for _ in range(height)]

    def set(self, x: int, y: int, char: str) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._cells[y][x] = char

    def get(self, x: int, y: int) -> str:
        return self._cells[y][x] if 0 <= x < self.width and 0 <= y < self.height else "."

    def rows(self) -> list[str]:
        return ["".join(r) for r in self._cells]

    # -- shapes --------------------------------------------------------------
    @staticmethod
    def ellipse(cx: float, cy: float, rx: float, ry: float) -> set[Cell]:
        """Pixels whose centre lies inside the ellipse. Symmetric about cx if cx is x.0."""
        cells = set()
        for y in range(math.floor(cy - ry), math.ceil(cy + ry) + 1):
            for x in range(math.floor(cx - rx), math.ceil(cx + rx) + 1):
                nx, ny = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                if nx * nx + ny * ny <= 1.0:
                    cells.add((x, y))
        return cells

    @staticmethod
    def rect(x0: int, y0: int, x1: int, y1: int) -> set[Cell]:
        return {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}

    # -- painting ------------------------------------------------------------
    def fill(self, cells: Iterable[Cell], char: str) -> None:
        for x, y in cells:
            self.set(x, y, char)

    def shade(
        self, cells: set[Cell], cx: float, cy: float, rx: float, ry: float, ramp: str
    ) -> None:
        """Paint cells as if they were a sphere-like form lit from LIGHT.

        ramp is four characters, darkest to lightest: shadow, base, light, highlight.
        """
        for x, y in cells:
            nx, ny = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
            nz = math.sqrt(max(0.0, 1.0 - nx * nx - ny * ny))
            lit = nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]
            if lit > _HIGHLIGHT:
                self.set(x, y, ramp[3])
            elif lit > _LIGHTER:
                self.set(x, y, ramp[2])
            elif lit > _BASE:
                self.set(x, y, ramp[1])
            else:
                self.set(x, y, ramp[0])

    def edge(self, cells: set[Cell], char: str = "d") -> None:
        """Draw a part's own edge in char (a soft separation line where parts overlap)."""
        for x, y in cells:
            if any((x + dx, y + dy) not in cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                self.set(x, y, char)

    def outer_outline(self, char: str = "O") -> None:
        """Outline the whole silhouette: any painted cell touching empty space becomes char.

        Parts overlap, so per-part outlines would draw hard lines through the figure. Parts
        get a soft edge (see edge) and only the union's outer boundary gets the dark outline.
        """
        edge = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.get(x, y) != "."
            and any(
                self.get(x + dx, y + dy) == "." for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
        ]
        for x, y in edge:
            self.set(x, y, char)

    def put(self, x: int, y: int, text: str) -> None:
        """Place a string of cells starting at (x, y); a space leaves a cell untouched."""
        for i, ch in enumerate(text):
            if ch != " ":
                self.set(x + i, y, ch)


def mirror_x(cells: Iterable[Cell], width: int) -> set[Cell]:
    return {(width - 1 - x, y) for x, y in cells}
