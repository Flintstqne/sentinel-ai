import pytest

from sentinel_ai.companion import sprites
from sentinel_ai.companion.animations import (
    FALL_FLAP_S,
    HELD_FLAP_S,
    SWAY_SWING,
    Pose,
    Walk,
    frame_spec,
    pose_spec,
    walk_spec,
)
from sentinel_ai.companion.physics import Mode
from sentinel_ai.companion.roaming import Facing
from sentinel_ai.companion.state_machine import State, Tone
from sentinel_ai.ui.theme import SPRITE_COLORS

ALL_CELLS = set(SPRITE_COLORS) | {"."}
POSES = set(sprites.POSE_FRAMES)  # held, falling and landing: not standing frames
FRONT = [n for n, f in sprites.FRAMES.items() if not f.side and n not in POSES]
AIRBORNE = [n for n in POSES if n.startswith(("held", "fall"))]
SIDE = [n for n, f in sprites.FRAMES.items() if f.side]


def mask(rows: list[str]) -> list[str]:
    return ["".join("." if c == "." else "#" for c in r) for r in rows]


@pytest.mark.parametrize("name", sorted(sprites.FRAMES))
@pytest.mark.parametrize("tone", list(Tone))
@pytest.mark.parametrize("flip", [False, True])
def test_every_frame_is_a_valid_grid(name: str, tone: Tone, flip: bool) -> None:
    rows = sprites.grid(name, tone, flip)
    assert len(rows) == sprites.HEIGHT
    assert all(len(r) == sprites.WIDTH for r in rows)
    assert set("".join(rows)) <= ALL_CELLS


FRONT_MASK = [
    "...........##...........",
    "...........##...........",
    "........########........",
    "......############......",
    ".....##############.....",
    "....################....",
    "....################....",
    "....################....",
    "....################....",
    "....################....",
    "....################....",
    ".....##############.....",
    "..####################..",
    "..####################..",
    ".######################.",
    ".######################.",
    ".######################.",
    ".######################.",
    "..####################..",
    "...##################...",
    ".......##########.......",
    ".......##########.......",
    "....#######..#######....",
    "...########..########...",
]

SIDE_MASK = [
    "...............##.......",
    "...............##.......",
    ".............#####......",
    "............#######.....",
    "...........#########....",
    "..........###########...",
    "..........###########...",
    "..........###########...",
    "..........##############",
    "..........##############",
    ".........###############",
    ".##...#############.....",
    ".#################......",
    "###################.....",
    "..#################.....",
    "..##################....",
    "..##################....",
    "...################.....",
    "...################.....",
    "....##############......",
    "......##########........",
    ".......###...###........",
    ".....############.......",
    ".....#############......",
]


def test_front_silhouette_matches_golden_outline() -> None:
    """Guards the character's identity: change the outline on purpose, update this mask."""
    assert mask(sprites.grid("base")) == FRONT_MASK


def test_side_silhouette_matches_golden_outline() -> None:
    assert mask(sprites.grid("walk_stand")) == SIDE_MASK


def test_front_views_are_symmetrical() -> None:
    rows = mask(sprites.grid("base"))
    assert all(r == r[::-1] for r in rows)


def test_front_outline_never_changes_below_the_head_overlays() -> None:
    # Overlays (beacon, spark, sleep marks) only touch rows 0-4; everything under is fixed.
    base = mask(sprites.grid("base"))
    for name in FRONT:
        assert mask(sprites.grid(name))[5:] == base[5:], name


def test_walk_poses_differ_only_in_the_legs() -> None:
    stand = sprites.grid("walk_stand")
    for name in ("walk_front", "walk_back"):
        pose = sprites.grid(name)
        legs = sprites.SIDE_LEG_ROWS[0]
        assert pose[:legs] == stand[:legs], name
        assert pose[legs:] != stand[legs:], f"{name} would look identical to standing"
    assert sprites.grid("walk_front")[legs:] != sprites.grid("walk_back")[legs:]


@pytest.mark.parametrize("name", sorted(set(sprites.FRAMES) - set(AIRBORNE)))
def test_feet_always_rest_on_the_same_baseline(name: str) -> None:
    # The duck must not hop when it turns or changes pose: the last row always has feet.
    assert set(sprites.grid(name)[-1]) - {"."}


@pytest.mark.parametrize("name", sorted(sprites.FRAMES))
def test_flip_is_an_exact_mirror(name: str) -> None:
    rows = sprites.grid(name)
    assert sprites.grid(name, flip=True) == [r[::-1] for r in rows]


def test_tone_recolours_only_visor_and_beacon() -> None:
    base, warn, crit = (
        sprites.grid("alert", t) for t in (Tone.NORMAL, Tone.WARNING, Tone.CRITICAL)
    )
    assert "W" in "".join(warn) and "R" not in "".join(warn)
    assert "R" in "".join(crit) and "W" not in "".join(crit)
    assert "W" not in "".join(base) and "R" not in "".join(base)
    undo = str.maketrans({"W": "V", "w": "v", "Y": "h"})
    assert [r.translate(undo) for r in warn] == base  # armour untouched


def test_sweep_visits_every_visor_column() -> None:
    assert set(sprites.SWEEP_ORDER) == set(range(len(sprites.FRONT_VISOR_COLS)))


def test_bill_is_not_the_warning_colour() -> None:
    # Amber is reserved for warnings; a slate bill keeps a normal duck from looking alarmed.
    assert not {"W", "w", "R", "r"} & set("".join(sprites.grid("base")))


# -- animations ----------------------------------------------------------------


@pytest.mark.parametrize("reduced", [False, True])
@pytest.mark.parametrize("state", list(State))
def test_every_state_resolves_to_a_real_frame_within_padding(state: State, reduced: bool) -> None:
    walks = [None, *(Walk(f, s) for f in Facing for s in range(0, 12))]
    for walk in walks:
        for ticks in range(0, 120):
            spec = frame_spec(state, ticks, reduced_motion=reduced, walk=walk)
            assert spec.name in sprites.FRAMES
            assert abs(spec.dx) <= 1 and -1 <= spec.dy <= 0  # pads left, right and top only


@pytest.mark.parametrize("state", [s for s in State if s is not State.IDLE])
def test_status_states_always_face_the_user_even_if_a_walk_is_supplied(state: State) -> None:
    for facing in (Facing.LEFT, Facing.RIGHT):
        for ticks in range(60):
            spec = frame_spec(state, ticks, walk=Walk(facing, 5))
            assert not sprites.FRAMES[spec.name].side and not spec.flip


@pytest.mark.parametrize("state", list(State))
def test_reduced_motion_is_fully_static_and_front_facing(state: State) -> None:
    specs = {
        frame_spec(state, t, reduced_motion=True, walk=Walk(Facing.LEFT, t)) for t in range(300)
    }
    assert len(specs) == 1
    (spec,) = specs
    assert (spec.dx, spec.dy, spec.flip) == (0, 0, False)
    assert not sprites.FRAMES[spec.name].side


def test_idle_stands_front_and_walks_side_on() -> None:
    standing = frame_spec(State.IDLE, 3, walk=Walk(Facing.FRONT, 0))
    assert not sprites.FRAMES[standing.name].side
    for facing, flipped in ((Facing.RIGHT, False), (Facing.LEFT, True)):
        spec = frame_spec(State.IDLE, 3, walk=Walk(facing, 0))
        assert sprites.FRAMES[spec.name].side and spec.flip is flipped


def test_walk_cycle_uses_every_pose_in_order_and_repeats() -> None:
    names = [walk_spec(Walk(Facing.RIGHT, s)).name for s in range(16)]
    assert set(names) == {"walk_stand", "walk_front", "walk_back"}
    expected = [p for p in sprites.WALK_POSES for _ in (0, 1)]  # each pose lasts two steps
    assert names[:8] == expected
    assert names[8:] == expected


def test_contact_poses_rest_on_the_ground_and_passing_poses_rise() -> None:
    assert walk_spec(Walk(Facing.RIGHT, 0)).dy == -1  # passing pose: body highest
    assert walk_spec(Walk(Facing.RIGHT, 2)).dy == 0  # contact pose: feet on the ground


def test_animation_is_deterministic() -> None:
    a = [frame_spec(State.IDLE, t, walk=Walk(Facing.LEFT, t)) for t in range(200)]
    b = [frame_spec(State.IDLE, t, walk=Walk(Facing.LEFT, t)) for t in range(200)]
    assert a == b


def test_idle_blinks_and_breathes_but_subtly() -> None:
    specs = [frame_spec(State.IDLE, t) for t in range(96)]
    assert {s.name for s in specs} == {"base", "blink"}
    assert sum(s.name == "blink" for s in specs) == 2  # once per 6 s, one tick long
    assert {s.dy for s in specs} == {0, -1}


# -- the held, falling and landing poses ---------------------------------------------


def test_every_pose_frame_exists_and_the_set_is_what_the_animations_expect() -> None:
    expected = {f"held_{s}_{f}" for s in "clr" for f in "ab"} | {"fall_a", "fall_b", "land"}
    assert set(sprites.POSE_FRAMES) == expected and expected <= set(sprites.FRAMES)


def test_the_standing_duck_is_unchanged_by_the_pose_builder() -> None:
    # The golden outline tests cover the mask; this guards the colours of the default build.
    assert sprites._build_front() == sprites._FRONT_BODY
    assert sprites._FRONT_BODY[0].count("O") == 2  # the antenna bulb is still there


def test_wing_positions_and_dangling_legs_differ_between_held_frames() -> None:
    a, b = sprites.grid("held_c_a"), sprites.grid("held_c_b")
    assert a != b  # the wings flap
    c, left, right = (sprites.grid(f"held_{s}_a") for s in "clr")
    legs = slice(20, 24)
    assert len({tuple(c[legs]), tuple(left[legs]), tuple(right[legs])}) == 3  # three swings
    assert c[:19] == left[:19] == right[:19]  # only the legs swing


def test_held_and_falling_figures_are_lifted_and_lose_the_antenna() -> None:
    base = sprites.grid("base")
    for name in sprites.POSE_FRAMES:
        if name == "land":
            continue
        rows = sprites.grid(name)
        assert "v" not in rows[0] and "v" not in rows[1], f"{name} should have no antenna bulb"
        assert base[0].count("O") == 2  # whereas the standing duck does


def test_the_lenses_sit_where_the_lifted_head_is() -> None:
    lit = {sprites.FRONT_VISOR_ROWS[0] - 2, sprites.FRONT_VISOR_ROWS[1] - 2}
    for name in ("held_c_a", "held_l_b", "fall_a", "fall_b"):
        rows = sprites.grid(name)
        for r in lit:
            assert all(rows[r][c] in "Vh" for c in sprites.FRONT_VISOR_COLS), (name, r)
    # and they are wide open, unlike the standing duck's resting lenses
    assert sprites.grid("base")[6][6] == "v"


def test_a_falling_duck_tucks_its_feet_and_a_held_one_lets_them_hang() -> None:
    held, fall = sprites.grid("held_c_a"), sprites.grid("fall_a")
    assert held[23].strip(".") and not fall[23].strip(".")  # feet at the bottom only when held
    assert held[20:23] != fall[20:23]


def test_landing_is_a_squash_with_closed_eyes_and_dust() -> None:
    base, land = sprites.grid("base"), sprites.grid("land")

    def head_top(rows: list[str]) -> int:
        return next(i for i, r in enumerate(rows) if "S" in r or "L" in r)

    assert head_top(land) > head_top(base)  # the head sits lower: squashed
    lens_top, lens_bottom = (r + 1 for r in sprites.FRONT_VISOR_ROWS)  # the squash shifts them
    assert land[lens_top][6] == "O" and land[lens_bottom][6] == "v"  # lenses shut
    dust = [(r, c) for r, row in enumerate(land) for c, ch in enumerate(row) if ch == "z"]
    assert len(dust) == 4 and {c < 12 for _, c in dust} == {True, False}  # a puff either side


def test_hot_spots_and_dust_follow_the_tone_rules_for_poses_too() -> None:
    warn = sprites.grid("held_c_a", Tone.WARNING)
    assert "Y" in "".join(warn) and "h" not in "".join(warn)


# -- pose -> frame --------------------------------------------------------------------


def test_a_grounded_duck_has_no_pose() -> None:
    with pytest.raises(ValueError):
        Pose(Mode.GROUNDED, 0.0)


def test_held_frames_flap_on_a_clock_and_swing_with_the_drag_speed() -> None:
    def name(clock: float, sway: float = 0.0) -> str:
        return pose_spec(Pose(Mode.HELD, clock, sway)).name

    assert name(0.0) == "held_c_a" and name(HELD_FLAP_S * 1.1) == "held_c_b"
    assert name(HELD_FLAP_S * 2.1) == "held_c_a"  # and back again
    assert name(0.0, SWAY_SWING + 50) == "held_l_a"  # dragged right: legs trail left
    assert name(0.0, -SWAY_SWING - 50) == "held_r_a"
    assert name(0.0, SWAY_SWING - 50) == "held_c_a"  # below the threshold: hanging straight


def test_falling_flaps_faster_than_carrying_and_landing_is_the_squash() -> None:
    assert pose_spec(Pose(Mode.FALLING, 0.0)).name == "fall_a"
    assert pose_spec(Pose(Mode.FALLING, FALL_FLAP_S * 1.1)).name == "fall_b"
    assert FALL_FLAP_S < HELD_FLAP_S
    assert pose_spec(Pose(Mode.LANDING, 0.0)).name == "land"


def test_reduced_motion_makes_the_held_pose_a_still_one() -> None:
    for clock in (0.0, 0.5, 1.7):
        spec = pose_spec(Pose(Mode.HELD, clock, 900.0), reduced_motion=True)
        assert spec.name == "held_c_a"


def test_a_pose_outranks_whatever_state_the_duck_was_in() -> None:
    held = Pose(Mode.HELD, 0.0)
    for state in State:
        spec = frame_spec(state, 7, walk=Walk(Facing.LEFT, 3), pose=held)
        assert spec.name.startswith("held_") and not spec.flip
    assert frame_spec(State.IDLE, 7, pose=None).name in sprites.FRAMES  # no pose: normal logic


def test_every_pose_resolves_to_a_real_frame_with_no_downward_offset() -> None:
    for mode in (Mode.HELD, Mode.FALLING, Mode.LANDING):
        for clock in (0.0, 0.05, 0.1, 0.3, 1.0):
            for sway in (-900.0, 0.0, 900.0):
                for reduced in (False, True):
                    spec = pose_spec(Pose(mode, clock, sway), reduced_motion=reduced)
                    assert spec.name in sprites.FRAMES and spec.dy <= 0
