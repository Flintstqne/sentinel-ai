import math
import random

import pytest

from sentinel_ai.companion.physics import (
    GRAVITY,
    LAND_TIME,
    SQUASH_MIN_SPEED,
    TERMINAL_SPEED,
    THROW_CAP,
    THROW_STALE,
    WALL_KEEP,
    Mode,
    Physics,
    World,
)

W = World(lo_x=0, hi_x=1000, top_y=0, floor_y=900)


def fall(p: Physics, dt: float = 1 / 60, limit: float = 10.0) -> float:
    t = 0.0
    while p.mode in (Mode.FALLING, Mode.HELD) and t < limit:
        p.step(dt)
        t += dt
    return t


def hold_then_release(
    p: Physics, x: float, y: float, moves: list[tuple[float, float, float]], release: float
) -> None:
    p.grab(0.0)
    for mx, my, t in moves:
        p.move_to(mx, my, t)
    p.release(release)


def test_starts_standing_on_the_floor_or_falling_if_placed_above_it() -> None:
    assert Physics(500, 900, W).mode is Mode.GROUNDED
    assert Physics(500, 300, W).mode is Mode.FALLING
    assert Physics(500, 5000, W).y == 900  # below the floor is clamped up to it


@pytest.mark.parametrize("height", [50, 200, 600, 850])
@pytest.mark.parametrize("dt", [1 / 120, 1 / 60, 1 / 30, 0.2])
def test_free_fall_matches_the_analytic_time_and_impact_speed(height, dt) -> None:
    p = Physics(500, 900 - height, W)
    t = fall(p, dt)
    assert p.y == 900 and p.mode in (Mode.LANDING, Mode.GROUNDED)
    expected_t = math.sqrt(2 * height / GRAVITY)
    expected_v = min(math.sqrt(2 * GRAVITY * height), TERMINAL_SPEED)
    assert t == pytest.approx(expected_t, abs=dt + 0.02)
    assert p.impact == pytest.approx(expected_v, rel=0.05)


def test_terminal_velocity_caps_the_fall_speed() -> None:
    tall = World(0, 1000, 0, 100_000)
    p = Physics(500, 0, tall)
    fall(p, 1 / 60, limit=60)
    assert p.impact <= TERMINAL_SPEED + 1e-9 and p.impact > 0.97 * TERMINAL_SPEED


def test_the_fall_speeds_up_to_terminal_velocity_and_then_stays_there() -> None:
    far = World(0, 1000, 0, 20_000)
    p = Physics(500, 0, far)
    fastest, t = 0.0, 0.0
    while p.mode is Mode.FALLING and t < 30:
        p.step(1 / 60)
        t += 1 / 60
        fastest = max(fastest, p.vy)
    assert fastest <= TERMINAL_SPEED + 1e-9 and fastest > 0.99 * TERMINAL_SPEED
    # time = time to reach terminal + remaining distance at constant speed
    reach = TERMINAL_SPEED / GRAVITY
    expected = reach + (20_000 - 0.5 * TERMINAL_SPEED * reach) / TERMINAL_SPEED
    assert t == pytest.approx(expected, abs=0.1)


def test_never_ends_up_below_the_floor_whatever_the_time_steps() -> None:
    rng = random.Random(7)
    for _ in range(300):
        p = Physics(rng.uniform(0, 1000), rng.uniform(0, 900), W)
        p.vx, p.vy = rng.uniform(-3000, 3000), rng.uniform(-3000, 3000)
        for _ in range(400):
            p.step(rng.choice([0.001, 1 / 60, 0.05, 0.3, 1.5]))
            assert 0 <= p.y <= 900 and 0 <= p.x <= 1000


def test_a_big_frame_gap_cannot_tunnel_through_the_floor() -> None:
    p = Physics(500, 0, W)
    p.step(5.0)  # five seconds in a single call
    assert p.y == 900 and p.mode in (Mode.LANDING, Mode.GROUNDED)


def test_releasing_on_the_floor_stands_without_falling() -> None:
    p = Physics(500, 900, W)
    p.grab(0.0)
    p.move_to(600, 900, 0.1)
    p.release(0.2)
    assert p.mode is Mode.GROUNDED and (p.vx, p.vy) == (0, 0) and p.y == 900


def test_releasing_a_still_pointer_mid_air_drops_straight_down() -> None:
    p = Physics(500, 500, W)
    p.grab(0.0)
    p.move_to(520, 400, 0.1)
    p.release(0.5)  # the pointer has been still for 0.4 s
    assert p.mode is Mode.FALLING and (p.vx, p.vy) == (0, 0)
    fall(p)
    assert p.x == 520


def test_a_throw_takes_the_pointer_velocity_from_the_last_moments() -> None:
    p = Physics(100, 400, W)
    moves = [(100 + 15 * i, 400 - 5 * i, 0.01 * i) for i in range(1, 7)]  # 1500 px/s right, 500 up
    hold_then_release(p, 100, 400, moves, 0.065)
    assert p.vx == pytest.approx(1500, rel=0.05) and p.vy == pytest.approx(-500, rel=0.1)


def test_throws_are_capped_and_stale_or_tiny_gestures_throw_nothing() -> None:
    fast = Physics(100, 400, W)
    hold_then_release(fast, 100, 400, [(100 + 400 * i, 400, 0.01 * i) for i in range(1, 5)], 0.045)
    assert math.hypot(fast.vx, fast.vy) == pytest.approx(THROW_CAP, rel=1e-6)
    # Two samples are inside the throw window, but the pointer stopped 70 ms before release.
    stale = Physics(100, 400, W)
    hold_then_release(
        stale, 100, 400, [(100, 400, 0.0), (300, 400, 0.02), (500, 400, 0.04)], 0.04 + 0.07
    )
    assert THROW_STALE < 0.07 and (stale.vx, stale.vy) == (0, 0)
    fresh = Physics(100, 400, W)  # the same gesture released at once does throw
    hold_then_release(fresh, 100, 400, [(100, 400, 0.0), (300, 400, 0.02), (500, 400, 0.04)], 0.045)
    assert fresh.vx > 1000
    tiny = Physics(100, 400, W)
    hold_then_release(tiny, 100, 400, [(110, 400, 0.005)], 0.006)
    assert (tiny.vx, tiny.vy) == (0, 0)


def test_the_walls_absorb_most_of_a_sideways_throw_and_the_duck_stays_inside() -> None:
    p = Physics(900, 300, W)
    p.vx = 1800.0
    p.step(0.1)  # reaches the wall inside this step
    assert p.x == 1000 and p.vx == pytest.approx(-1800 * WALL_KEEP, rel=0.01)
    fall(p)
    assert 0 <= p.x <= 1000


def test_the_ceiling_stops_an_upward_throw() -> None:
    p = Physics(500, 100, W)
    p.vy = -3000.0
    p.step(0.3)
    assert p.y >= 0
    fall(p)
    assert p.y == 900


def test_only_a_hard_landing_squashes_and_it_recovers_after_a_moment() -> None:
    hard = Physics(500, 100, W)
    fall(hard)
    assert hard.impact >= SQUASH_MIN_SPEED and hard.mode is Mode.LANDING and hard.squashed
    hard.step(LAND_TIME * 0.5)
    assert hard.mode is Mode.LANDING
    hard.step(LAND_TIME)
    assert hard.mode is Mode.GROUNDED and hard.vx == 0
    soft = Physics(500, 880, W)  # a 20 px hop
    fall(soft)
    assert soft.impact < SQUASH_MIN_SPEED and soft.mode is Mode.GROUNDED and not soft.squashed


def test_a_landing_slides_a_little_in_the_direction_of_travel_then_stops() -> None:
    p = Physics(400, 300, W)
    p.vx = 600.0
    fall(p)
    landed_at = p.x
    p.step(LAND_TIME * 2)
    assert p.x > landed_at and p.vx == 0 and p.mode is Mode.GROUNDED
    stopped = p.x
    p.step(1.0)
    assert p.x == stopped


def test_sway_follows_the_pointer_and_relaxes_when_it_rests() -> None:
    p = Physics(300, 400, W)
    p.grab(0.0)
    for i in range(1, 10):
        p.move_to(300 + 20 * i, 400, 0.01 * i)
    assert p.sway > 500
    for i in range(1, 10):
        p.move_to(500 - 20 * i, 400, 0.1 + 0.01 * i)
    assert p.sway < -500
    p.step(1.0)
    assert abs(p.sway) < 5
    p.release(2.0)
    assert p.sway == 0


def test_held_duck_is_clamped_to_the_world_and_ignores_moves_when_not_held() -> None:
    p = Physics(500, 500, W)
    p.move_to(0, 0, 0.1)
    assert (p.x, p.y) == (500, 500)  # not held: pointer moves mean nothing
    p.grab(0.2)
    p.move_to(-999, 9999, 0.3)
    assert (p.x, p.y) == (0, 900)
    p.move_to(9999, -999, 0.4)
    assert (p.x, p.y) == (1000, 0)


def test_stand_at_and_snap_to_floor() -> None:
    p = Physics(500, 100, W)
    p.stand_at(2000)
    assert (p.x, p.y, p.mode) == (1000, 900, Mode.GROUNDED)
    q = Physics(500, 100, W)
    q.snap_to_floor()
    assert (q.x, q.y, q.mode) == (500, 900, Mode.GROUNDED)


def test_a_standing_duck_follows_the_floor_when_it_moves_and_a_falling_one_keeps_falling() -> None:
    p = Physics(500, 900, W)
    p.set_world(World(0, 1000, 0, 800))  # the panel got taller
    assert p.y == 800
    q = Physics(500, 100, W)
    q.set_world(World(0, 1000, 0, 1200))  # a taller screen
    assert q.mode is Mode.FALLING
    fall(q)
    assert q.y == 1200


def test_the_simulation_is_deterministic() -> None:
    def run() -> list[tuple[float, float]]:
        p = Physics(300, 100, W)
        p.vx, p.vy = 700.0, -400.0
        out = []
        for _ in range(240):
            p.step(1 / 60)
            out.append((p.x, p.y))
        return out

    assert run() == run()


def test_release_is_ignored_when_not_held() -> None:
    p = Physics(500, 500, W)
    p.release(1.0)
    assert p.mode is Mode.FALLING and (p.vx, p.vy) == (0, 0)
