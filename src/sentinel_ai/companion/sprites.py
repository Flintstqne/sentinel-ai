"""Pixel data for the Sentinel companion: a small robotic duck. Pure Python, no Qt.

Two views share one 24x24 canvas and one feet baseline (the bottom row), so the duck does
not jump when it turns:

* FRONT: facing the user. Used when standing and for every status state, so the glowing
  goggle lenses are always readable.
* SIDE: facing right, used for the walk cycle. Facing left is the same grid flipped.

The bodies are built from shaded ellipses (see pixelart.py) so the curves are clean and the
lighting is consistent; details are placed by hand on top. Frames then differ only in the
lens cells, a few overlay cells and, in the side view, the legs, so the duck stays
recognisable in every state.

The bill is deliberately slate-metal, not amber: amber means "warning" in this design
system and a permanently amber bill would read as a permanent warning.

Cell legend (colours in ui/theme.py SPRITE_COLORS):
  .  transparent   O  outline        d  slate shadow   S  slate base    L  slate light
  H  slate highlight   A/a  teal trim (dim/bright)   B/b  bill and feet (lit/shadow)
  V  lens lit   v  lens dim   h  lens hot spot   z  sleep marker
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel_ai.companion.pixelart import Canvas
from sentinel_ai.companion.state_machine import Tone

WIDTH = 24
HEIGHT = 24

# Two goggle lenses of four cells each, two rows tall (top row dim, bottom row bright).
FRONT_VISOR_ROWS = (6, 7)
FRONT_VISOR_COLS = (6, 7, 8, 9, 14, 15, 16, 17)
SIDE_VISOR_ROWS = (5, 6)
SIDE_VISOR_COLS = (15, 16, 17, 18)
SIDE_LEG_ROWS = (21, 22, 23)

_RAMP = "dSLH"  # shadow, base, light, highlight


def _part(c: Canvas, cx: float, cy: float, rx: float, ry: float, ramp: str = _RAMP) -> None:
    cells = Canvas.ellipse(cx, cy, rx, ry)
    c.shade(cells, cx, cy, rx, ry, ramp)
    c.edge(cells, "d")


def _build_front(
    *,
    lift: int = 0,
    squash: bool = False,
    wings: str = "down",
    legs: str = "splay",
    leg_shift: int = 0,
) -> tuple[str, ...]:
    """The front-facing body.

    lift raises the whole figure (it is being held, so the legs get longer and the antenna
    is dropped); squash is the landing pose; wings is "down", "up" or "out"; legs is
    "splay" (standing), "dangle" (held) or "tuck" (falling) and leg_shift swings dangling legs.
    The defaults reproduce the standing duck exactly.
    """
    c = Canvas(WIDTH, HEIGHT)
    oy = -lift + (1 if squash else 0)  # vertical offset of the head, body and wings
    body_cy, body_rx, body_ry = 16 + oy, (9.2 if squash else 8.5), (5.0 if squash else 5.8)
    if legs == "splay":
        for mirrored in (False, True):  # webbed feet, splayed outward
            fx = (lambda x: WIDTH - 1 - x) if mirrored else (lambda x: x)
            for x, ch in zip(range(7, 10), "dSd", strict=True):
                c.set(fx(x), 21, ch)
            for x, ch in zip(range(4, 11), "dBBBBBd", strict=True):
                c.set(fx(x), 22, ch)
            for x, ch in zip(range(3, 11), "dBdBdBdB", strict=True):
                c.set(fx(x), 23, ch)
    else:
        last_body_row = int(body_cy + body_ry)
        for x0 in (8 + leg_shift, 13 + leg_shift):
            if legs == "dangle":  # long legs hanging straight down, feet at the bottom
                for y in range(last_body_row + 1, 23):
                    c.put(x0, y, "dSd")
                c.put(x0 - 1, 23, "dBBBd")
            else:  # "tuck": feet pulled up under the body
                c.put(x0 - 1, last_body_row + 1, "dBBBd")
    if squash:
        wing_params = [(3.4, 16.4, 3.0, 3.4), (20.6, 16.4, 3.0, 3.4)]
    elif wings == "up":  # raised, beside the head
        wing_params = [(2.0, 10.5 + oy, 2.4, 5.0), (22.0, 10.5 + oy, 2.4, 5.0)]
    elif wings == "out":  # held out level
        wing_params = [(2.4, 14.5 + oy, 3.6, 2.4), (21.6, 14.5 + oy, 3.6, 2.4)]
    else:
        wing_params = [(3.8, 15.8 + oy, 2.6, 4.0), (20.2, 15.8 + oy, 2.6, 4.0)]
    for cx, cy, rx, ry in wing_params:
        _part(c, cx, cy, rx, ry, "ddSL")
    _part(c, 12, body_cy, body_rx, body_ry)  # body
    belly = Canvas.ellipse(12, 17 + oy, 4.5, 3.6 if squash else 4.2)
    c.shade(belly, 12, 17 + oy, 4.5, 3.6 if squash else 4.2, "SLLH")
    c.edge(belly, "S")
    c.put(11, 14 + oy, "AA")  # chest power core
    c.put(10, 15 + oy, "AaaA")
    c.put(10, 16 + oy, "AhaA")
    c.put(11, 17 + oy, "AA")
    _part(c, 12, 8 + oy, 8.8 if squash else 8.2, 5.8 if squash else 6.4)  # head
    for x0 in (5, 13):  # goggle frames; lens cells are filled per frame
        c.put(x0, 5 + oy, "OOOOOO")
        c.put(x0, 8 + oy, "OOOOOO")
        for y in (6, 7):
            c.set(x0, y + oy, "O")
            c.set(x0 + 5, y + oy, "O")
    c.put(11, 6 + oy, "OO")  # bridge
    c.put(11, 7 + oy, "OO")
    c.put(6, 9 + oy, "BBBBBBBBBBBB")  # wide flat bill
    c.put(6, 10 + oy, "BBBbBBBBbBBB")
    c.put(7, 11 + oy, "bbbbbbbbbb")
    c.put(9, 12 + oy, "bbbbbb")
    if lift == 0:  # antenna bulb (it has no room when the figure is lifted)
        c.put(11, 0 + (1 if squash else 0), "OO")
        c.put(11, 1 + (1 if squash else 0), "vv")
    c.outer_outline()
    return tuple(c.rows())


def _build_side() -> tuple[str, ...]:
    c = Canvas(WIDTH, HEIGHT)
    tail = {
        (1, 11), (2, 11), (1, 12), (2, 12), (3, 12), (0, 13), (1, 13), (2, 13), (3, 13),
        (4, 13), (2, 14), (3, 14), (4, 14), (5, 14),
    }  # fmt: skip
    c.shade(tail, 2.5, 13, 3, 3, "dSLL")
    c.edge(tail, "d")
    _part(c, 11, 16, 8.7, 5.6)  # body
    wing = Canvas.ellipse(10, 16, 5.2, 3.4)
    c.shade(wing, 10, 16, 5.2, 3.4, "ddSL")
    c.edge(wing, "d")
    c.put(7, 16, "aaaaaaa")  # wing stripe
    _part(c, 15, 11.5, 3.2, 4.2)  # neck
    _part(c, 15.5, 7.5, 5.6, 5.6)  # head
    c.put(14, 4, "OOOOOO")  # goggle frame; lens cells are filled per frame
    c.put(14, 7, "OOOOOO")
    for y in (5, 6):
        c.set(14, y, "O")
        c.set(19, y, "O")
    c.put(19, 8, "BBBBB")  # bill
    c.put(19, 9, "BbBBB")
    c.put(20, 10, "bbbb")
    c.put(15, 0, "OO")
    c.put(15, 1, "vv")
    c.outer_outline()
    return tuple(c.rows())


_FRONT_BODY = _build_front()
_SIDE_BODY = _build_side()

# Poses off the ground: held by the user, falling, and the squash on landing. Each tuple is
# (body builder arguments, lens rows offset). The body is lifted when held or falling.
_HELD_SWING = {"c": 0, "l": -2, "r": 2}  # legs trail the drag: moving right swings them left
_FRONT_VARIANTS: dict[str, tuple[str, ...]] = {}
for _swing, _shift in _HELD_SWING.items():
    _FRONT_VARIANTS[f"held_{_swing}_a"] = _build_front(
        lift=2, wings="up", legs="dangle", leg_shift=_shift
    )
    _FRONT_VARIANTS[f"held_{_swing}_b"] = _build_front(
        lift=2, wings="out", legs="dangle", leg_shift=_shift
    )
_FRONT_VARIANTS["fall_a"] = _build_front(lift=2, wings="up", legs="tuck")
_FRONT_VARIANTS["fall_b"] = _build_front(lift=2, wings="out", legs="tuck")
_FRONT_VARIANTS["land"] = _build_front(squash=True)
POSE_FRAMES = tuple(_FRONT_VARIANTS)


def _legs(back_shift: int, front_shift: int) -> tuple[str, str, str]:
    """Rows 21-23 of the side view for a given back-leg and front-leg offset."""
    c = Canvas(WIDTH, HEIGHT)
    for base, shift in ((7, back_shift), (13, front_shift)):
        c.put(base + shift, 21, "dSd")
        c.put(base - 2 + shift, 22, "dBBBBd")
        c.put(base - 2 + shift, 23, "dBdBdBd")
    rows = c.rows()
    return rows[21], rows[22], rows[23]


_LEGS = {
    "stand": _legs(0, 0),
    "front_forward": _legs(-1, 1),  # stride open: front foot ahead, back foot behind
    "back_forward": _legs(1, -1),  # feet gathered under the body
}

_Overlay = tuple[tuple[int, int, str], ...]

# Front view: the sparkle sits left of the head, sleep marks to its right.
_SPARK_FULL: _Overlay = (
    (3, 3, "h"), (3, 2, "V"), (3, 4, "V"), (2, 3, "V"), (4, 3, "V"),
)  # fmt: skip
_SPARK_SMALL: _Overlay = ((3, 3, "h"),)
_BEACON: _Overlay = ((0, 11, "h"), (0, 12, "h"), (1, 11, "V"), (1, 12, "V"))
_SLEEP0: _Overlay = ((4, 20, "z"),)
_SLEEP1: _Overlay = ((4, 20, "z"), (2, 21, "z"), (2, 22, "z"))


@dataclass(frozen=True)
class Frame:
    side: bool
    visor: tuple[str, str]
    overlay: _Overlay = ()
    legs: tuple[str, str, str] | None = None
    body: str = ""  # key into _FRONT_VARIANTS for non-standing front poses
    dy: int = 0  # how far the lens cells sit from FRONT_VISOR_ROWS in that body


def _front(top: str, bottom: str, overlay: _Overlay = ()) -> Frame:
    assert len(top) == len(bottom) == len(FRONT_VISOR_COLS)
    return Frame(False, (top, bottom), overlay)


def _side(legs: tuple[str, str, str]) -> Frame:
    return Frame(True, ("vvvv", "VhVV"), (), legs)


def _sweep_order(width: int) -> tuple[int, ...]:
    return (*range(width), *range(width - 2, 0, -1))


SWEEP_ORDER = _sweep_order(len(FRONT_VISOR_COLS))

# Walk-cycle poses in order; two contact poses around two passing (stand) poses.
WALK_POSES = ("walk_stand", "walk_front", "walk_stand", "walk_back")


def _frames() -> dict[str, Frame]:
    w = len(FRONT_VISOR_COLS)
    dim, lit = "v" * w, "V" * w
    frames: dict[str, Frame] = {
        "base": _front(dim, "VhVVVVhV"),
        "blink": _front("O" * w, dim),
        "alert": _front(lit, "VVhVVhVV", _BEACON),
        "alert_dim": _front(dim, dim),
        "insight": _front(lit, "VVhVVhVV", _SPARK_FULL),
        "insight_b": _front(lit, "VVhVVhVV", _SPARK_SMALL),
        "happy": _front("vVVvvVVv", "VvvVVvvV"),  # two ^ ^ eyes
        "sleep0": _front("O" * w, "OvvOOvvO", _SLEEP0),
        "sleep1": _front("O" * w, "OvvOOvvO", _SLEEP1),
        "walk_stand": _side(_LEGS["stand"]),
        "walk_front": _side(_LEGS["front_forward"]),
        "walk_back": _side(_LEGS["back_forward"]),
    }
    wide = ("V" * w, "VhVVVVhV")  # wide-eyed while being carried or dropped
    for swing in _HELD_SWING:
        for flap in "ab":
            frames[f"held_{swing}_{flap}"] = Frame(False, wide, body=f"held_{swing}_{flap}", dy=-2)
    for flap in "ab":
        frames[f"fall_{flap}"] = Frame(False, wide, body=f"fall_{flap}", dy=-2)
    frames["land"] = Frame(  # squashed, eyes shut, a puff of dust either side
        False, ("O" * w, dim), ((22, 2, "z"), (21, 1, "z"), (22, 21, "z"), (21, 22, "z")),
        body="land", dy=1,
    )  # fmt: skip
    for p in range(w):
        row = "".join("V" if i == p else "v" for i in range(w))
        frames[f"scan{p}"] = _front(row, row)
    for seg in range(3):
        row = "".join("V" if i * 3 // w == seg else "v" for i in range(w))
        frames[f"think{seg}"] = _front(dim, row)
    return frames


FRAMES = _frames()

_TONE_MAP = {
    Tone.NORMAL: {},
    Tone.WARNING: {"V": "W", "v": "w", "h": "Y"},
    Tone.CRITICAL: {"V": "R", "v": "r", "h": "X"},
}


def grid(name: str, tone: Tone = Tone.NORMAL, flip: bool = False) -> list[str]:
    """Return a frame as HEIGHT strings of WIDTH cells. flip mirrors it (face left)."""
    frame = FRAMES[name]
    if frame.body:
        base = _FRONT_VARIANTS[frame.body]
    elif frame.side:
        base = _SIDE_BODY
    else:
        base = _FRONT_BODY
    rows = [list(r) for r in base]
    if frame.legs is not None:
        for y, text in zip(SIDE_LEG_ROWS, frame.legs, strict=True):
            rows[y] = list(text)
    v_rows, v_cols = (
        (SIDE_VISOR_ROWS, SIDE_VISOR_COLS) if frame.side else (FRONT_VISOR_ROWS, FRONT_VISOR_COLS)
    )
    for row, text in zip(v_rows, frame.visor, strict=True):
        for col, cell in zip(v_cols, text, strict=True):
            rows[row + frame.dy][col] = cell
    for row, col, cell in frame.overlay:
        rows[row][col] = cell
    mapping = _TONE_MAP[tone]
    out = ["".join(mapping.get(c, c) for c in r) for r in rows]
    return [r[::-1] for r in out] if flip else out
