"""Maps (state, ticks-in-state, optional walk) to a sprite frame and a pixel offset.

Pure and deterministic. Every status state uses the front-facing view so the visor is always
readable; only an idle, walking duck turns side-on. With reduced motion every state gets one
static front frame and no offset, so state is still readable but nothing moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel_ai.companion.physics import Mode
from sentinel_ai.companion.roaming import Facing
from sentinel_ai.companion.sprites import SWEEP_ORDER, WALK_POSES
from sentinel_ai.companion.state_machine import State

_SHAKE = (-1, 1, -1, 1, 0, 0)
_BLINK_PERIOD = 48  # ticks (6 s); the blink lands on the last-but-three tick of each period
_BREATH_PERIOD = 8  # ticks per half-breath
_STEPS_PER_POSE = 2  # the walk cycle advances every other step (4 poses = 1 full stride)


@dataclass(frozen=True)
class FrameSpec:
    name: str
    dx: int = 0  # offsets are in sprite cells, not screen pixels
    dy: int = 0  # never positive: nothing may dip below the feet baseline
    flip: bool = False  # mirror horizontally (walking left)


@dataclass(frozen=True)
class Walk:
    facing: Facing
    steps: int  # total steps taken so far; selects the pose


@dataclass(frozen=True)
class Pose:
    """The duck is off the ground: held by the user, falling, or just landed."""

    mode: Mode
    clock: float  # seconds since the pose began; drives the wing flapping
    sway: float = 0.0  # horizontal pointer speed while held, px/s

    def __post_init__(self) -> None:
        if self.mode is Mode.GROUNDED:
            raise ValueError("a grounded duck has no pose")


SWAY_SWING = 240.0  # px/s of pointer speed before the dangling legs visibly trail
HELD_FLAP_S = 0.14  # seconds per wing position while carried
FALL_FLAP_S = 0.09  # and while falling: faster, in a panic


def pose_spec(pose: Pose, *, reduced_motion: bool = False) -> FrameSpec:
    if pose.mode is Mode.LANDING:
        return FrameSpec("land")
    flap_s = HELD_FLAP_S if pose.mode is Mode.HELD else FALL_FLAP_S
    flap = "a" if reduced_motion or int(pose.clock / flap_s) % 2 == 0 else "b"
    if pose.mode is Mode.FALLING:
        return FrameSpec(f"fall_{flap}")
    # Legs trail the drag: moving right swings them to the left.
    swing = "c"
    if not reduced_motion:
        if pose.sway > SWAY_SWING:
            swing = "l"
        elif pose.sway < -SWAY_SWING:
            swing = "r"
    return FrameSpec(f"held_{swing}_{flap}")


_STATIC = {
    State.IDLE: "base",
    State.SCANNING: "scan5",
    State.ALERT: "alert",
    State.THINKING: "think1",
    State.INSIGHT: "insight",
    State.HAPPY: "happy",
    State.SLEEPING: "sleep0",
}


def walk_spec(walk: Walk) -> FrameSpec:
    pose = WALK_POSES[(walk.steps // _STEPS_PER_POSE) % len(WALK_POSES)]
    # The body is highest in the passing pose, which reads as a waddle. Contact poses rest on
    # the ground line (dy 0).
    dy = -1 if pose == "walk_stand" else 0
    return FrameSpec(pose, dy=dy, flip=walk.facing is Facing.LEFT)


def frame_spec(
    state: State,
    ticks: int,
    *,
    reduced_motion: bool = False,
    walk: Walk | None = None,
    pose: Pose | None = None,
) -> FrameSpec:
    if pose is not None:  # being carried or dropped outranks whatever the duck was doing
        return pose_spec(pose, reduced_motion=reduced_motion)
    if reduced_motion:
        return FrameSpec(_STATIC[state])
    if state is State.IDLE:
        if walk is not None and walk.facing is not Facing.FRONT:
            return walk_spec(walk)
        name = "blink" if ticks % _BLINK_PERIOD == _BLINK_PERIOD - 4 else "base"
        return FrameSpec(name, dy=-((ticks // _BREATH_PERIOD) % 2))
    if state is State.SCANNING:
        return FrameSpec(f"scan{SWEEP_ORDER[ticks % len(SWEEP_ORDER)]}")
    if state is State.ALERT:
        name = "alert" if (ticks // 2) % 2 == 0 else "alert_dim"
        return FrameSpec(name, dx=_SHAKE[ticks] if ticks < len(_SHAKE) else 0)
    if state is State.THINKING:
        return FrameSpec(f"think{(ticks // 2) % 3}")
    if state is State.INSIGHT:
        return FrameSpec("insight" if (ticks // 3) % 2 == 0 else "insight_b")
    if state is State.HAPPY:
        return FrameSpec("happy", dy=-1 if ticks < 8 and ticks % 4 < 2 else 0)
    return FrameSpec("sleep0" if (ticks // 8) % 2 == 0 else "sleep1")
