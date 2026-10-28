import random

import pytest

from sentinel_ai.companion.roaming import Facing, Wanderer

STEP = 4


def make(seed: int = 1, x: int = 100, lo: int = 0, hi: int = 400) -> Wanderer:
    return Wanderer(x, lo, hi, STEP, random.Random(seed))


def run(w: Wanderer, ticks: int) -> list[tuple[int, Facing]]:
    out = []
    for _ in range(ticks):
        w.tick()
        out.append((w.x, w.facing))
    return out


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("bounds", [(0, 400), (0, 40), (50, 53), (0, 0), (-300, 100)])
def test_never_leaves_bounds(seed: int, bounds: tuple[int, int]) -> None:
    lo, hi = bounds
    w = make(seed, x=lo + (hi - lo) // 2, lo=lo, hi=hi)
    for x, _ in run(w, 4000):
        assert lo <= x <= hi


def test_no_room_means_no_walking() -> None:
    w = make(x=50, lo=50, hi=50)
    assert {f for _, f in run(w, 2000)} == {Facing.FRONT}
    assert w.steps == 0


def test_moves_exactly_one_step_per_tick_and_only_when_facing_a_side() -> None:
    w = make(seed=3)
    prev = w.x
    walked = 0
    for _ in range(3000):
        w.tick()
        delta = w.x - prev
        if delta:
            walked += 1
            assert abs(delta) == STEP or w.x in w.bounds  # clamped moves may be shorter
            assert (
                w.facing is (Facing.LEFT if delta < 0 else Facing.RIGHT) or w.facing is Facing.FRONT
            )
        prev = w.x
    assert walked > 100


def test_facing_front_means_standing_still() -> None:
    """The duck never slides while facing the user: movement always shows a side pose."""
    w = make(seed=5)
    history = run(w, 4000)
    moved_while_front = [
        i
        for (x0, _), (x1, f1), i in zip(history, history[1:], range(len(history)), strict=False)
        if x1 != x0 and f1 is Facing.FRONT
    ]
    assert moved_while_front == []


def test_stands_for_a_beat_at_each_edge_before_turning() -> None:
    w = make(seed=2, x=8, lo=0, hi=400)
    history = run(w, 6000)
    edge_hits = [i for i, (x, f) in enumerate(history) if x in (0, 400) and f is Facing.FRONT]
    assert edge_hits, "never reached an edge in 6000 ticks"
    i = edge_hits[0]
    assert all(f is Facing.FRONT for _, f in history[i : i + 8])  # the minimum turn pause


def test_uses_both_directions() -> None:
    faces = {f for _, f in run(make(seed=4, x=200), 4000)}
    assert {Facing.LEFT, Facing.RIGHT, Facing.FRONT} <= faces


def test_same_seed_same_walk_different_seed_different_walk() -> None:
    assert run(make(7), 1500) == run(make(7), 1500)
    assert run(make(7), 1500) != run(make(8), 1500)


def test_pause_stops_and_faces_front_then_resumes() -> None:
    w = make(seed=6)
    run(w, 400)  # make sure it has been walking at some point
    w.pause(200)  # longer than the longest possible stand (80), or an ignored pause would pass
    here = w.x
    for _ in range(200):
        w.tick()
        assert (w.x, w.facing) == (here, Facing.FRONT)
    assert any(x != here for x, _ in run(w, 600))


def test_place_clamps_and_stands() -> None:
    w = make(seed=1, x=100, lo=0, hi=400)
    w.place(9999)
    assert w.x == 400 and w.facing is Facing.FRONT and not w.walking
    w.place(-50)
    assert w.x == 0


def test_set_bounds_pulls_the_duck_back_inside() -> None:
    w = make(x=300, lo=0, hi=400)
    w.set_bounds(0, 120)
    assert w.x == 120
    w.set_bounds(500, 100)  # inverted bounds collapse rather than break
    assert w.bounds[0] == w.bounds[1] == w.x == 500


def test_hold_stops_a_walk_in_place() -> None:
    w = make(seed=9)
    for _ in range(2000):
        w.tick()
        if w.walking:
            break
    assert w.walking
    here = w.x
    w.hold()
    assert not w.walking and w.facing is Facing.FRONT and w.x == here


def test_step_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Wanderer(0, 0, 10, 0, random.Random(0))


def test_shrinking_bounds_mid_walk_never_leaves_the_new_bounds() -> None:
    """A monitor or resolution change can shrink the work area while the duck is walking."""
    w = make(seed=11, x=300, lo=0, hi=400)
    for _ in range(5000):
        w.tick()
        if w.walking:
            break
    assert w.walking
    w.set_bounds(0, w.x - STEP // 2 - 1)  # the next step would now overshoot
    lo, hi = w.bounds
    for x, _ in run(w, 500):
        assert lo <= x <= hi
