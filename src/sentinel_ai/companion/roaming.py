"""Where the duck walks while idle. Pure Python, seeded, no Qt.

The wanderer alternates between standing (facing the user) and walking along a horizontal
line, one sprite cell per tick so it never leaves the pixel grid. It never leaves
[lo, hi]; on reaching an edge it stops, stands for a moment (the "turn") and later walks
back. All randomness comes from the injected random.Random, so a seed reproduces a walk.
"""

from __future__ import annotations

import random
from enum import Enum

_STAND_TICKS = (24, 80)  # 3 to 10 s at 8 ticks per second
_WALK_STEPS = (16, 96)
_TURN_TICKS = (8, 16)


class Facing(Enum):
    FRONT = "front"
    LEFT = "left"
    RIGHT = "right"


class Wanderer:
    def __init__(self, x: int, lo: int, hi: int, step: int, rng: random.Random) -> None:
        if step <= 0:
            raise ValueError("step must be positive")
        self._step = step
        self._rng = rng
        self._lo, self._hi = lo, max(lo, hi)
        self._x = min(max(x, self._lo), self._hi)
        self._facing = Facing.FRONT
        self._walk_left = 0  # steps remaining in the current walk
        self._stand_left = self._roll(_STAND_TICKS)
        self._pause_left = 0
        self._steps = 0

    # -- read-only view ------------------------------------------------------
    @property
    def x(self) -> int:
        return self._x

    @property
    def facing(self) -> Facing:
        return self._facing

    @property
    def walking(self) -> bool:
        """True while shown side-on: mid-walk, or on the tick after the last step."""
        return self._facing is not Facing.FRONT

    @property
    def steps(self) -> int:
        """Total steps taken; drives the walk-cycle frame."""
        return self._steps

    @property
    def bounds(self) -> tuple[int, int]:
        return self._lo, self._hi

    # -- control -------------------------------------------------------------
    def set_bounds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = lo, max(lo, hi)
        self._x = min(max(self._x, self._lo), self._hi)

    def place(self, x: int) -> None:
        """The user put the duck somewhere: stop and stand there."""
        self._x = min(max(x, self._lo), self._hi)
        self._stand()

    def pause(self, ticks: int) -> None:
        """Stop and face front for at least `ticks` ticks (after a click, say)."""
        self._pause_left = max(self._pause_left, ticks)
        self._stand()

    def hold(self) -> None:
        """Something else owns the duck right now (alert, drag...): stop where it is."""
        if self.walking:
            self._stand()

    def tick(self) -> None:
        if self._pause_left > 0:
            self._pause_left -= 1
            return
        if self._walk_left > 0:
            self._walk_one_step()
        elif self.walking:
            self._facing = Facing.FRONT  # settle: turn to face the user, standing still
        else:
            self._stand_left -= 1
            if self._stand_left <= 0:
                self._start_walk()

    # -- internals -----------------------------------------------------------
    def _roll(self, span: tuple[int, int]) -> int:
        return self._rng.randint(*span)

    def _stand(self) -> None:
        self._walk_left = 0
        self._facing = Facing.FRONT
        self._stand_left = self._roll(_STAND_TICKS)

    def _start_walk(self) -> None:
        room_left = (self._x - self._lo) // self._step
        room_right = (self._hi - self._x) // self._step
        if room_left + room_right == 0:  # nowhere to go
            self._stand_left = self._roll(_STAND_TICKS)
            return
        go_left = room_right == 0 or (
            room_left > 0 and self._rng.random() < room_left / (room_left + room_right)
        )
        room = room_left if go_left else room_right
        self._facing = Facing.LEFT if go_left else Facing.RIGHT
        self._walk_left = min(self._roll(_WALK_STEPS), room)

    def _walk_one_step(self) -> None:
        direction = -1 if self._facing is Facing.LEFT else 1
        self._x = min(max(self._x + direction * self._step, self._lo), self._hi)
        self._steps += 1
        self._walk_left -= 1
        at_edge = self._x in (self._lo, self._hi) or not self._has_room(direction)
        if self._walk_left <= 0 or at_edge:
            self._walk_left = 0  # facing is kept for one more tick so the last step is side-on
            # Reaching an edge is a turn: a short beat facing the user before heading back.
            self._stand_left = self._roll(_TURN_TICKS) if at_edge else self._roll(_STAND_TICKS)

    def _has_room(self, direction: int) -> bool:
        return self._lo <= self._x + direction * self._step <= self._hi
