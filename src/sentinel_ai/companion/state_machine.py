"""Companion behaviour as an explicit, Qt-free state machine.

Time is measured in fixed ticks (TICK_MS), so every animation is a pure function of
(state, ticks-in-state). That makes behaviour deterministic and testable.

Rules:
* IDLE, SCANNING and THINKING are sustained: they last until another request.
* ALERT, INSIGHT and HAPPY are transient: they play for a fixed number of ticks, then
  return to the sustained "base" state. A transient is never cut short by a lower or
  equal priority transient, and sustained requests during a transient only update the
  base state it returns to.
* SLEEPING means monitoring is paused. It is entered and left only through
  set_paused(); every request is refused while paused.
"""

from __future__ import annotations

from enum import Enum

TICK_MS = 125


class State(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    ALERT = "alert"
    THINKING = "thinking"
    INSIGHT = "insight"
    HAPPY = "happy"
    SLEEPING = "sleeping"


class Tone(Enum):
    NORMAL = "normal"
    WARNING = "warning"  # amber
    CRITICAL = "critical"  # red, confirmed high severity


_SUSTAINED = {State.IDLE, State.SCANNING, State.THINKING}
_TRANSIENT_TICKS = {State.ALERT: 16, State.INSIGHT: 24, State.HAPPY: 16}
_PRIORITY = {State.ALERT: 3, State.INSIGHT: 2, State.HAPPY: 1}


class CompanionStateMachine:
    def __init__(self) -> None:
        self._state = State.IDLE
        self._base = State.IDLE
        self._tone = Tone.NORMAL
        self._ticks = 0
        self._paused = False
        self._acc_ms = 0

    @property
    def state(self) -> State:
        return self._state

    @property
    def tone(self) -> Tone:
        return self._tone

    @property
    def ticks(self) -> int:
        """Ticks spent in the current state."""
        return self._ticks

    @property
    def paused(self) -> bool:
        return self._paused

    def request(self, state: State, tone: Tone = Tone.NORMAL) -> bool:
        """Ask for a state. Returns True if it was accepted (now or as the next base)."""
        if self._paused or state is State.SLEEPING:
            return False
        if state in _SUSTAINED:
            self._base = state
            if self._state not in _TRANSIENT_TICKS:
                self._enter(state)
            return True
        current = _PRIORITY.get(self._state, 0)
        upgrade = (
            state is State.ALERT
            and self._state is State.ALERT
            and tone is Tone.CRITICAL
            and self._tone is not Tone.CRITICAL
        )
        if not upgrade and (_PRIORITY[state] < current or (_PRIORITY[state] == current > 0)):
            return False
        self._enter(state, tone if state is State.ALERT else Tone.NORMAL)
        return True

    def finish(self) -> None:
        """The work that drove SCANNING/THINKING is done: settle back to IDLE."""
        if self._paused:
            return
        self._base = State.IDLE
        if self._state in _SUSTAINED:
            self._enter(State.IDLE)

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._enter(State.SLEEPING if paused else self._base)

    def tick(self) -> None:
        self._ticks += 1
        limit = _TRANSIENT_TICKS.get(self._state)
        if limit is not None and self._ticks >= limit:
            self._enter(self._base)

    def advance(self, dt_ms: int) -> int:
        """Feed wall-clock time; runs whole ticks. Returns how many ticks ran."""
        self._acc_ms += max(0, dt_ms)
        steps, self._acc_ms = divmod(self._acc_ms, TICK_MS)
        for _ in range(steps):
            self.tick()
        return steps

    def _enter(self, state: State, tone: Tone = Tone.NORMAL) -> None:
        self._state = state
        self._tone = tone
        self._ticks = 0
