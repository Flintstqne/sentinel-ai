"""Drag, throw and fall: the duck as a small rigid body. Pure Python, no Qt.

All times are seconds and all lengths are screen pixels, passed in explicitly, so the model
is deterministic and testable without a display or a clock.

Modes
* GROUNDED: standing on the floor (the wanderer owns it from here).
* HELD: carried by the pointer. Position follows the pointer, clamped to the world.
* FALLING: released above the floor. Gravity, the throw velocity from the last moments of
  the drag, walls that absorb most of the speed, a ceiling, and a terminal velocity.
* LANDING: just hit the floor hard enough to squash; slides a little and recovers.
A short hop lands straight back to GROUNDED with no squash.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum


class Mode(Enum):
    GROUNDED = "grounded"
    HELD = "held"
    FALLING = "falling"
    LANDING = "landing"


@dataclass(frozen=True)
class World:
    """The box the duck lives in: x range, the ceiling and the floor (all window top-left)."""

    lo_x: float
    hi_x: float
    top_y: float
    floor_y: float


GRAVITY = 3200.0  # px/s^2: a brisk, cartoonish fall
TERMINAL_SPEED = 2600.0
THROW_CAP = 2200.0  # px/s
THROW_WINDOW = 0.10  # s of pointer history used to estimate the throw
THROW_STALE = 0.06  # s: a pointer that stopped this long before release throws nothing
WALL_KEEP = 0.35  # fraction of horizontal speed kept after hitting a wall
SLIDE_DECAY = 9.0  # 1/s, horizontal slide after landing
SQUASH_MIN_SPEED = 500.0  # px/s impact speed that earns a squash
LAND_TIME = 0.26  # s spent squashed
SWAY_DECAY = 6.0  # 1/s, the carried-sway estimate relaxing when the pointer stops
MAX_STEP = 0.05  # s: longer frames are stepped in pieces so nothing tunnels through the floor


class Physics:
    def __init__(self, x: float, y: float, world: World) -> None:
        self.world = world
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.mode = Mode.GROUNDED
        self.impact = 0.0  # speed of the last floor impact
        self.sway = 0.0  # smoothed horizontal pointer speed while held, px/s
        self._land_left = 0.0
        self._samples: deque[tuple[float, float, float]] = deque()
        self._last_move: tuple[float, float, float] | None = None
        self.clamp()
        if self.y < self.world.floor_y - 0.5:
            self.mode = Mode.FALLING

    # -- the world -----------------------------------------------------------
    def set_world(self, world: World) -> None:
        self.world = world
        if self.mode is Mode.GROUNDED:
            self.y = world.floor_y
        self.clamp()

    def clamp(self) -> None:
        w = self.world
        self.x = min(max(self.x, w.lo_x), max(w.lo_x, w.hi_x))
        self.y = min(max(self.y, w.top_y), w.floor_y)

    @property
    def airborne(self) -> bool:
        return self.mode in (Mode.HELD, Mode.FALLING)

    @property
    def squashed(self) -> bool:
        return self.mode is Mode.LANDING

    # -- the user ------------------------------------------------------------
    def grab(self, now: float) -> None:
        self.mode = Mode.HELD
        self.vx = self.vy = 0.0
        self.sway = 0.0
        self._samples.clear()
        self._samples.append((now, self.x, self.y))
        self._last_move = (now, self.x, self.y)

    def move_to(self, x: float, y: float, now: float) -> None:
        """The pointer carried the duck to (x, y)."""
        if self.mode is not Mode.HELD:
            return
        prev_t, prev_x, _ = self._last_move or (now, x, y)
        self.x, self.y = x, y
        self.clamp()
        dt = now - prev_t
        if dt > 1e-4:
            instant = (self.x - prev_x) / dt
            blend = 1.0 - math.exp(-dt * 18.0)  # ~55 ms smoothing
            self.sway += (instant - self.sway) * blend
        self._last_move = (now, self.x, self.y)
        self._samples.append((now, self.x, self.y))
        while self._samples and now - self._samples[0][0] > THROW_WINDOW:
            self._samples.popleft()

    def release(self, now: float) -> None:
        """Let go. At the floor the duck just stands; anywhere else it is thrown with the
        pointer's velocity from the last moments of the drag, and falls."""
        if self.mode is not Mode.HELD:
            return
        vx, vy = self._throw_velocity(now)
        self.vx = vx
        self.vy = vy
        self.sway = 0.0
        if self.y >= self.world.floor_y - 0.5:
            self.y = self.world.floor_y
            self.vx = self.vy = 0.0
            self.mode = Mode.GROUNDED
        else:
            self.mode = Mode.FALLING

    def _throw_velocity(self, now: float) -> tuple[float, float]:
        recent = [s for s in self._samples if now - s[0] <= THROW_WINDOW]
        if len(recent) < 2 or now - recent[-1][0] > THROW_STALE:
            return 0.0, 0.0
        (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
        span = t1 - t0
        if span < 0.015:
            return 0.0, 0.0
        vx, vy = (x1 - x0) / span, (y1 - y0) / span
        speed = math.hypot(vx, vy)
        if speed > THROW_CAP:
            vx, vy = vx * THROW_CAP / speed, vy * THROW_CAP / speed
        return vx, vy

    def stand_at(self, x: float) -> None:
        """Stand on the floor at x (walking, or a finished landing)."""
        self.x = x
        self.vx = self.vy = 0.0
        self.mode = Mode.GROUNDED
        self.y = self.world.floor_y
        self.clamp()

    def snap_to_floor(self) -> None:
        """Reduced motion: no fall, the duck is simply standing on the floor."""
        self.y = self.world.floor_y
        self.vx = self.vy = 0.0
        self.mode = Mode.GROUNDED

    # -- time ----------------------------------------------------------------
    def step(self, dt: float) -> None:
        while dt > 0:
            piece = min(dt, MAX_STEP)
            self._step(piece)
            dt -= piece

    def _step(self, dt: float) -> None:
        if self.mode is Mode.HELD:
            self.sway *= math.exp(-SWAY_DECAY * dt)  # relaxes while the pointer is still
        elif self.mode is Mode.FALLING:
            self._fall(dt)
        elif self.mode is Mode.LANDING:
            self.x += self.vx * dt
            self.vx *= math.exp(-SLIDE_DECAY * dt)
            self.clamp()
            self._land_left -= dt
            if self._land_left <= 0:
                self.vx = 0.0
                self.mode = Mode.GROUNDED

    def _fall(self, dt: float) -> None:
        w = self.world
        prev_y, prev_vy = self.y, self.vy
        self.vy = min(prev_vy + GRAVITY * dt, TERMINAL_SPEED)
        self.x += self.vx * dt
        # Average velocity over the step: exact for constant gravity (using the end-of-step
        # velocity overstates the distance by a large fraction on a short fall).
        self.y += 0.5 * (prev_vy + self.vy) * dt
        if self.x < w.lo_x or self.x > w.hi_x:
            self.x = min(max(self.x, w.lo_x), w.hi_x)
            self.vx = -self.vx * WALL_KEEP
        if self.y < w.top_y:
            self.y = w.top_y
            self.vy = max(self.vy, 0.0)  # bumped the ceiling: starts falling again
        if self.y >= w.floor_y:
            # Speed at the moment of contact from v^2 = v0^2 + 2 g d (exact for constant
            # gravity), not the speed at the end of the step, which overshoots.
            drop = w.floor_y - prev_y
            self.impact = min(math.sqrt(prev_vy * prev_vy + 2 * GRAVITY * drop), TERMINAL_SPEED)
            self.y = w.floor_y
            self.vy = 0.0
            if self.impact >= SQUASH_MIN_SPEED:
                self.mode = Mode.LANDING
                self._land_left = LAND_TIME
            else:
                self.vx = 0.0
                self.mode = Mode.GROUNDED
