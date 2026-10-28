import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest

from sentinel_ai.companion import sprites
from sentinel_ai.companion.physics import Mode
from sentinel_ai.companion.roaming import Facing
from sentinel_ai.companion.state_machine import State, Tone
from sentinel_ai.companion.window import (
    CLICK_PAUSE_TICKS,
    CompanionWindow,
    bottom_ground_y,
    clamp_to_screens,
)
from sentinel_ai.platform import x11


@pytest.fixture
def window(qapp, monkeypatch):
    # Never run wmctrl/xprop against the offscreen test window.
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=False, roam=False)
    w.move(100, 100)
    w.show()
    yield w
    w.close()


def spy(signal) -> QSignalSpy:
    return QSignalSpy(signal)


def center(w: CompanionWindow) -> QPoint:
    return QPoint(w.width() // 2, w.height() // 2)


def test_window_flags_keep_it_behind_and_off_the_taskbar(window) -> None:
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnBottomHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.Tool


def test_dragging_carries_the_duck_and_letting_go_mid_air_makes_it_fall_to_the_floor(
    window,
) -> None:
    moved = spy(window.moved_by_user)
    clicked = spy(window.single_clicked)
    start = window.pos()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=center(window))
    QTest.mouseMove(window, center(window) + QPoint(30, 20))
    assert window.mode is Mode.HELD and window.pos() == start + QPoint(30, 20)
    assert window.current_spec().name.startswith("held_")  # dangling from the pointer
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=center(window) + QPoint(30, 20))
    assert window.mode is Mode.FALLING and window.current_spec().name.startswith("fall_")
    assert moved.count() == 0  # it has not come to rest yet
    for _ in range(300):
        window.physics_step(1 / 60)
        if window.mode is Mode.GROUNDED:
            break
    assert window.mode is Mode.GROUNDED
    assert window.pos() == QPoint(start.x() + 30, window.floor_y)  # on the floor, x kept
    assert moved.count() == 1 and moved.at(0)[0] == window.pos()
    assert not window.current_spec().name.startswith(("held_", "fall_", "land"))
    QTest.qWait(150)
    assert clicked.count() == 0  # a drag is not a click


def test_tiny_jitter_is_a_click_not_a_drag(window, qapp) -> None:
    moved = spy(window.moved_by_user)
    clicked = spy(window.single_clicked)
    start = window.pos()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=center(window))
    QTest.mouseMove(window, center(window) + QPoint(1, 1))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=center(window))
    QTest.qWait(qapp.doubleClickInterval() + 100)
    assert window.pos() == start
    assert moved.count() == 0 and clicked.count() == 1


def test_double_click_opens_dashboard_without_a_single_click(window, qapp) -> None:
    clicked = spy(window.single_clicked)
    doubled = spy(window.double_clicked)
    QTest.mouseDClick(window, Qt.MouseButton.LeftButton, pos=center(window))
    QTest.qWait(qapp.doubleClickInterval() + 100)
    assert doubled.count() == 1
    assert clicked.count() == 0


def test_state_requests_repaint_only_when_picture_changes(window) -> None:
    window.request_state(State.SCANNING)
    first = window._last
    window._refresh()
    assert window._last == first
    window.request_state(State.ALERT, Tone.CRITICAL)
    assert window._last != first


def test_pause_puts_companion_to_sleep(window) -> None:
    window.set_paused(True)
    assert window.machine.state is State.SLEEPING
    assert not window.request_state(State.ALERT, Tone.CRITICAL)
    window.set_paused(False)
    assert window.machine.state is State.IDLE


def test_reduced_motion_window_never_offsets_the_sprite(qapp, monkeypatch) -> None:
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=3, reduced_motion=True)
    w.request_state(State.ALERT, Tone.WARNING)
    for _ in range(20):
        w.machine.tick()
        assert (w.current_spec().dx, w.current_spec().dy) == (0, 0)


def test_clamp_keeps_window_on_screen() -> None:
    screens = [QRect(0, 0, 1920, 1080), QRect(1920, 0, 1280, 1024)]
    size = (72, 88)
    assert clamp_to_screens(QPoint(-500, -500), size, screens) == QPoint(0, 0)
    assert clamp_to_screens(QPoint(1900, 1070), size, screens) == QPoint(1848, 992)
    assert clamp_to_screens(QPoint(2000, 100), size, screens) == QPoint(2000, 100)
    assert clamp_to_screens(QPoint(7, 7), size, []) == QPoint(7, 7)


def test_placement_verification_logs_without_reserved_key_clash(qapp, monkeypatch, caplog) -> None:
    """Regression: logging extra={"message": ...} raised KeyError on a live Cinnamon run."""
    import logging

    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    monkeypatch.setattr(x11, "request_desktop_layer", lambda wid, run=None: True)
    states = frozenset(x11.WANTED_STATES)
    monkeypatch.setattr(
        x11,
        "read_placement",
        lambda wid, run=None: x11.PlacementResult(True, "xprop", states, "ok"),
    )
    w = CompanionWindow(scale=3, reduced_motion=True)
    with caplog.at_level(logging.INFO, logger="sentinel_ai.companion.window"):
        w._apply_desktop_layer()
        w._verify_placement()
    assert w.placement is not None and w.placement.ok
    assert any(r.getMessage() == "desktop placement" for r in caplog.records)


# -- roaming ---------------------------------------------------------------------


@pytest.fixture
def roamer(qapp, monkeypatch):
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=False, roam=True, seed=1)
    area = w.work_area()
    w.place(QPoint(area.left() + 100, bottom_ground_y(area, w.height())))
    return w


def tick(w: CompanionWindow, n: int) -> None:
    for _ in range(n):
        w._on_tick()


def test_bottom_ground_y_rests_the_feet_on_the_bottom_edge() -> None:
    area = QRect(0, 0, 1920, 1040)  # work area above a 40 px panel
    y = bottom_ground_y(area, 72)
    assert y == 968
    assert y + 72 == area.bottom() + 1  # the window's bottom edge IS the ground


def test_default_position_is_on_the_bottom_line(qapp, monkeypatch) -> None:
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=False)
    area = w.work_area()
    assert w.default_position().y() == bottom_ground_y(area, w.height())


def test_idle_duck_walks_along_the_floor_and_stays_on_screen(roamer) -> None:
    area = roamer.work_area()
    seen = set()
    for _ in range(3000):
        tick(roamer, 1)
        assert roamer.y() == roamer.floor_y
        assert area.left() <= roamer.x() <= area.right() - roamer.width() + 1
        seen.add(roamer.x())
    assert len(seen) > 20, "the duck never moved"


def test_walking_shows_side_sprites_facing_the_direction_of_travel(roamer) -> None:
    saw = set()
    for _ in range(3000):
        before = roamer.x()
        tick(roamer, 1)
        spec = roamer.current_spec()
        if roamer.x() != before:
            assert sprites.FRAMES[spec.name].side
            assert spec.flip == (roamer.x() < before)
            saw.add(roamer.x() < before)
        elif not roamer._wanderer.walking:
            assert not sprites.FRAMES[spec.name].side  # standing faces the user
    assert saw == {True, False}, "should have walked both left and right"


def test_an_alert_stops_the_walk_and_faces_the_user(roamer) -> None:
    for _ in range(4000):
        tick(roamer, 1)
        if roamer._wanderer.walking:
            break
    assert roamer._wanderer.walking
    roamer.request_state(State.ALERT, Tone.WARNING)
    here = roamer.x()
    for _ in range(12):
        tick(roamer, 1)
        assert roamer.x() == here
        assert not sprites.FRAMES[roamer.current_spec().name].side
    # once the alert is over it is idle again and wanders on
    tick(roamer, 40)
    assert roamer.machine.state is State.IDLE
    moved = {roamer.x()}
    for _ in range(3000):
        tick(roamer, 1)
        moved.add(roamer.x())
    assert len(moved) > 1


def test_reduced_motion_and_roam_off_never_move_the_window(qapp, monkeypatch) -> None:
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    for kwargs in (
        {"reduced_motion": True, "roam": True},
        {"reduced_motion": False, "roam": False},
    ):
        w = CompanionWindow(scale=4, seed=1, **kwargs)
        w.place(QPoint(100, 100), settle=True)
        start = w.pos()
        assert start.y() == w.floor_y
        tick(w, 3000)
        assert w.pos() == start
        assert Facing.FRONT is w._wanderer.facing


def test_turning_roam_off_mid_walk_stops_in_place(roamer) -> None:
    for _ in range(4000):
        tick(roamer, 1)
        if roamer._wanderer.walking:
            break
    here = roamer.pos()
    roamer.set_roam(False)
    tick(roamer, 500)
    assert roamer.pos() == here
    roamer.set_roam(True)
    assert roamer.roam


def test_the_floor_is_always_the_bottom_whatever_height_the_duck_was_dropped_at(window) -> None:
    window.set_roam(True)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=center(window))
    QTest.mouseMove(window, center(window) + QPoint(10, -40))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=center(window) + QPoint(10, -40))
    for _ in range(400):
        window.physics_step(1 / 60)
    assert window.mode is Mode.GROUNDED and window.y() == window.floor_y


def test_a_click_makes_the_duck_stand_still_for_a_few_seconds(roamer) -> None:
    for _ in range(4000):
        tick(roamer, 1)
        if roamer._wanderer.walking:
            break
    QTest.mouseClick(roamer, Qt.MouseButton.LeftButton, pos=center(roamer))
    here = roamer.pos()
    for _ in range(CLICK_PAUSE_TICKS):
        tick(roamer, 1)
        assert roamer.pos() == here


# -- picking the duck up, throwing it, dropping it ------------------------------------


@pytest.fixture
def floored(qapp, monkeypatch):
    """A duck standing on the floor, roaming off, so only the user moves it."""
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=False, roam=False, seed=1)
    w.show()
    area = w.work_area()
    w.place(QPoint(area.left() + 200, bottom_ground_y(area, w.height())), settle=True)
    yield w
    w.close()


def grab_and_drop(w: CompanionWindow, to: QPoint, release_at: float = 1.0) -> None:
    """Pick the duck up and put it down at `to`, with the pointer resting before release."""
    w._press_global = QPoint(0, 0)
    w._dragging = True
    w._begin_hold(0.0)
    w._physics.move_to(to.x(), to.y(), 0.5)
    w.move(round(w._physics.x), round(w._physics.y))
    w._press_global, w._dragging = None, False
    w._let_go(release_at)


def land(w: CompanionWindow, seconds: float = 6.0) -> None:
    for _ in range(int(seconds * 60)):
        w.physics_step(1 / 60)
        if w.mode is Mode.GROUNDED:
            return


def test_letting_go_on_the_floor_just_stands(floored) -> None:
    moved = spy(floored.moved_by_user)
    start_x = floored.x()
    grab_and_drop(floored, QPoint(start_x + 80, floored.floor_y))
    assert floored.mode is Mode.GROUNDED and not floored._phys_timer.isActive()
    assert floored.pos() == QPoint(start_x + 80, floored.floor_y)
    assert moved.count() == 1 and moved.at(0)[0] == floored.pos()


def test_a_drop_from_height_ends_squashed_then_standing(floored) -> None:
    top = floored.work_area().top()
    grab_and_drop(floored, QPoint(floored.x(), top + 10))
    assert floored.mode is Mode.FALLING
    names = set()
    for _ in range(600):
        floored.physics_step(1 / 60)
        names.add(floored.current_spec().name)
        if floored.mode is Mode.GROUNDED:
            break
    assert "land" in names, "a hard landing should squash"
    assert any(n.startswith("fall_") for n in names)
    assert floored.mode is Mode.GROUNDED and floored.y() == floored.floor_y


def test_a_short_hop_lands_without_the_squash(floored) -> None:
    grab_and_drop(floored, QPoint(floored.x(), floored.floor_y - 4))
    names = set()
    for _ in range(120):
        floored.physics_step(1 / 60)
        names.add(floored.current_spec().name)
    assert "land" not in names and floored.mode is Mode.GROUNDED


def test_a_thrown_duck_carries_the_pointer_velocity_and_stays_on_screen(floored) -> None:
    area = floored.work_area()
    floored._press_global, floored._dragging = QPoint(0, 0), True
    floored._begin_hold(0.0)
    y = floored.floor_y - 200
    for step in range(1, 7):  # sweep right at about 1500 px/s, then release mid-sweep
        floored._physics.move_to(floored.x() + step * 15, y, step * 0.01)
    floored.move(round(floored._physics.x), round(floored._physics.y))
    floored._press_global, floored._dragging = None, False
    floored._let_go(0.065)
    assert floored._physics.vx > 800
    thrown_from = floored.x()
    land(floored)
    assert floored.x() > thrown_from + 100  # it travelled
    assert area.left() <= floored.x() <= area.right() - floored.width() + 1
    assert floored.y() == floored.floor_y


def test_the_dangling_legs_trail_the_drag_direction(floored) -> None:
    floored._press_global, floored._dragging = QPoint(0, 0), True
    floored._begin_hold(0.0)
    for step in range(1, 8):  # dragging right quickly
        floored._physics.move_to(floored.x() + step * 20, floored.floor_y - 100, step * 0.01)
    assert floored.current_spec().name.startswith("held_l_")  # legs swing left
    for step in range(1, 8):
        floored._physics.move_to(floored.x() - step * 20, floored.floor_y - 100, 0.2 + step * 0.01)
    assert floored.current_spec().name.startswith("held_r_")
    floored._physics.step(1.0)  # the pointer rests: the sway relaxes
    assert floored.current_spec().name.startswith("held_c_")


def test_the_wings_flap_while_carried(floored) -> None:
    floored._press_global, floored._dragging = QPoint(0, 0), True
    floored._begin_hold(0.0)
    flaps = set()
    for _ in range(40):
        QTest.qWait(20)
        flaps.add(floored.current_spec().name[-1])
    assert flaps == {"a", "b"}


def test_the_duck_cannot_be_dragged_below_the_floor_or_off_the_side(floored) -> None:
    area = floored.work_area()
    floored._press_global, floored._dragging = QPoint(0, 0), True
    floored._begin_hold(0.0)
    floored._physics.move_to(-5000, 99999, 0.1)
    assert floored._physics.y == floored.floor_y and floored._physics.x >= area.left()
    floored._physics.move_to(99999, -5000, 0.2)
    assert floored._physics.x <= area.right() - floored.width() + 1
    assert floored._physics.y >= area.top()


def test_the_falling_duck_can_be_caught_mid_air(floored) -> None:
    grab_and_drop(floored, QPoint(floored.x(), floored.work_area().top() + 10))
    for _ in range(10):
        floored.physics_step(1 / 60)
    assert floored.mode is Mode.FALLING
    QTest.mousePress(floored, Qt.MouseButton.LeftButton, pos=center(floored))
    QTest.mouseMove(floored, center(floored) + QPoint(12, 0))
    assert floored.mode is Mode.HELD
    QTest.mouseRelease(floored, Qt.MouseButton.LeftButton, pos=center(floored) + QPoint(12, 0))


def test_reduced_motion_skips_the_fall_but_still_ends_on_the_floor(qapp, monkeypatch) -> None:
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=True, roam=True)
    w.show()
    area = w.work_area()
    w.place(QPoint(area.left() + 100, bottom_ground_y(area, w.height())), settle=True)
    moved = spy(w.moved_by_user)
    grab_and_drop(w, QPoint(w.x(), area.top() + 20))
    assert w.mode is Mode.GROUNDED and w.y() == w.floor_y and moved.count() == 1
    w._press_global, w._dragging = QPoint(0, 0), True
    w._begin_hold(0.0)
    assert w.current_spec().name == "held_c_a"  # a still pose: no flapping, no swinging
    w.close()


def test_placing_the_duck_in_mid_air_makes_it_fall_but_settling_does_not(qapp, monkeypatch) -> None:
    monkeypatch.setattr(x11, "session_type", lambda env=None: "unknown")
    w = CompanionWindow(scale=4, reduced_motion=False, roam=False)
    area = w.work_area()
    w.place(QPoint(area.left() + 50, area.top() + 50))
    assert w.mode is Mode.FALLING
    land(w)
    assert w.mode is Mode.GROUNDED and w.y() == w.floor_y
    w.place(QPoint(area.left() + 50, area.top() + 50), settle=True)
    assert w.mode is Mode.GROUNDED and w.y() == w.floor_y


def test_roaming_resumes_only_after_the_duck_has_landed(floored) -> None:
    floored.set_roam(True)
    grab_and_drop(floored, QPoint(floored.x(), floored.work_area().top() + 10))
    before = floored.x()
    for _ in range(400):  # far longer than any stand: it would have walked off by now
        tick(floored, 1)
    assert floored.mode is Mode.FALLING and floored.x() == before
    land(floored)
    seen = set()
    for _ in range(3000):
        tick(floored, 1)
        seen.add(floored.x())
    assert len(seen) > 5 and floored.y() == floored.floor_y
