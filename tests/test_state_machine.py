from sentinel_ai.companion.state_machine import TICK_MS, CompanionStateMachine, State, Tone


def run(machine: CompanionStateMachine, ticks: int) -> None:
    for _ in range(ticks):
        machine.tick()


def test_starts_idle() -> None:
    m = CompanionStateMachine()
    assert (m.state, m.tone, m.ticks, m.paused) == (State.IDLE, Tone.NORMAL, 0, False)


def test_sustained_states_persist() -> None:
    m = CompanionStateMachine()
    assert m.request(State.SCANNING)
    run(m, 500)
    assert m.state is State.SCANNING
    m.finish()
    assert m.state is State.IDLE


def test_transient_returns_to_base() -> None:
    m = CompanionStateMachine()
    m.request(State.SCANNING)
    m.request(State.HAPPY)
    assert m.state is State.HAPPY
    run(m, 16)
    assert m.state is State.SCANNING


def test_sustained_request_during_transient_only_changes_base() -> None:
    m = CompanionStateMachine()
    m.request(State.ALERT, Tone.WARNING)
    assert m.request(State.THINKING)
    assert m.state is State.ALERT
    run(m, 16)
    assert m.state is State.THINKING


def test_priority_blocks_lower_transient() -> None:
    m = CompanionStateMachine()
    m.request(State.ALERT, Tone.WARNING)
    assert not m.request(State.INSIGHT)
    assert not m.request(State.HAPPY)
    assert m.state is State.ALERT


def test_higher_priority_interrupts() -> None:
    m = CompanionStateMachine()
    m.request(State.HAPPY)
    assert m.request(State.ALERT, Tone.WARNING)
    assert m.state is State.ALERT


def test_critical_upgrades_warning_alert_but_not_vice_versa() -> None:
    m = CompanionStateMachine()
    m.request(State.ALERT, Tone.WARNING)
    run(m, 5)
    assert m.request(State.ALERT, Tone.CRITICAL)
    assert (m.tone, m.ticks) == (Tone.CRITICAL, 0)
    assert not m.request(State.ALERT, Tone.WARNING)
    assert m.tone is Tone.CRITICAL


def test_tone_only_applies_to_alert() -> None:
    m = CompanionStateMachine()
    m.request(State.INSIGHT, Tone.CRITICAL)
    assert m.tone is Tone.NORMAL
    m.request(State.ALERT, Tone.CRITICAL)
    run(m, 16)
    assert m.tone is Tone.NORMAL


def test_pause_sleeps_refuses_requests_and_resumes_to_base() -> None:
    m = CompanionStateMachine()
    m.request(State.SCANNING)
    m.set_paused(True)
    assert m.state is State.SLEEPING
    assert not m.request(State.ALERT, Tone.CRITICAL)
    assert not m.request(State.THINKING)
    run(m, 100)
    assert m.state is State.SLEEPING
    m.set_paused(False)
    assert m.state is State.SCANNING


def test_sleeping_cannot_be_requested_directly() -> None:
    m = CompanionStateMachine()
    assert not m.request(State.SLEEPING)
    assert m.state is State.IDLE


def test_pause_cancels_a_playing_transient() -> None:
    m = CompanionStateMachine()
    m.request(State.ALERT, Tone.CRITICAL)
    m.set_paused(True)
    assert (m.state, m.tone) == (State.SLEEPING, Tone.NORMAL)


def test_advance_accumulates_partial_ticks() -> None:
    m = CompanionStateMachine()
    assert m.advance(TICK_MS - 1) == 0
    assert m.advance(1) == 1
    assert m.advance(TICK_MS * 3 + 10) == 3
    assert m.advance(-500) == 0


def test_deterministic_trace() -> None:
    def trace() -> list[tuple[State, int]]:
        m = CompanionStateMachine()
        out = []
        script = [State.SCANNING, State.ALERT, State.THINKING, State.HAPPY, State.INSIGHT]
        for i in range(120):
            if i % 15 == 0:
                m.request(script[(i // 15) % len(script)], Tone.WARNING)
            m.tick()
            out.append((m.state, m.ticks))
        return out

    assert trace() == trace()
