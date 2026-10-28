"""The desktop companion: a frameless, draggable, always-behind-windows sprite.

While idle it wanders along the floor (the bottom edge of the work area). Pick it up and it
dangles from the pointer; let go anywhere but the floor and it is thrown with the pointer's
velocity and falls. Anything else that owns the duck (an alert, a drag, a click, reduced
motion, the toggle) stops it where it stands.

Two clocks: a slow 8 Hz tick drives behaviour and idle animation, and a 60 Hz physics timer
that runs only while the duck is held or airborne, so a standing duck costs almost nothing.
"""

from __future__ import annotations

import logging
import random
import time

from PySide6.QtCore import QElapsedTimer, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from sentinel_ai.companion.animations import FrameSpec, Pose, Walk, frame_spec
from sentinel_ai.companion.physics import Mode, Physics, World
from sentinel_ai.companion.render import PAD_TOP_CELLS, PAD_X_CELLS, canvas_size, render_frame
from sentinel_ai.companion.roaming import Wanderer
from sentinel_ai.companion.state_machine import TICK_MS, CompanionStateMachine, State, Tone
from sentinel_ai.platform import x11

log = logging.getLogger(__name__)

DRAG_THRESHOLD_PX = 4
DEFAULT_MARGIN = 48
VERIFY_DELAY_MS = 400  # Muffin applies state changes asynchronously
CLICK_PAUSE_TICKS = 40  # stand still for 5 s after a click so the panel stays next to it
MENU_PAUSE_TICKS = 80
PHYSICS_INTERVAL_MS = 16  # about 60 frames per second while held or airborne


def clamp_to_screens(pos: QPoint, size: tuple[int, int], screens: list[QRect]) -> QPoint:
    """Keep the window fully visible: nudge it into the screen it overlaps most."""
    w, h = size
    window = QRect(pos.x(), pos.y(), w, h)

    def overlap(r: QRect) -> int:
        inter = r.intersected(window)
        return inter.width() * inter.height() if inter.isValid() else 0

    target = max(screens, key=overlap, default=None)
    if target is None:
        return pos
    x = min(max(pos.x(), target.left()), target.right() - w + 1)
    y = min(max(pos.y(), target.top()), target.bottom() - h + 1)
    return QPoint(x, y)


def bottom_ground_y(area: QRect, height: int) -> int:
    """Window y that rests the duck's feet on the bottom edge of the work area.

    The canvas has no padding under the feet, so the window's bottom edge is the ground and
    the window manager has nothing to push back.
    """
    return area.bottom() + 1 - height


class CompanionWindow(QWidget):
    single_clicked = Signal()
    double_clicked = Signal()
    moved_by_user = Signal(QPoint)  # emitted when the duck comes to rest after being moved

    def __init__(
        self,
        scale: int,
        reduced_motion: bool,
        menu: QMenu | None = None,
        roam: bool = True,
        seed: int | None = None,
    ) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnBottomHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Sentinel AI companion")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._scale = scale
        self._reduced = reduced_motion
        self._roam = roam
        self._menu = menu
        self.machine = CompanionStateMachine()
        self.placement: x11.PlacementResult | None = None
        self._wmctrl_ok = False
        self.setFixedSize(*canvas_size(scale))

        # One cell per tick keeps the walk on the sprite's pixel grid.
        self._wanderer = Wanderer(0, 0, 0, scale, random.Random(seed))
        self._physics = Physics(0, 0, World(0, 0, 0, 0))
        self._notify_landing = False  # the user moved the duck; tell the app where it came to rest

        self._press_global: QPoint | None = None
        self._press_origin = QPoint()
        self._dragging = False
        self._last: tuple[FrameSpec, Tone] | None = None

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.single_clicked)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._on_tick)

        self._phys_timer = QTimer(self)
        self._phys_timer.setInterval(PHYSICS_INTERVAL_MS)
        self._phys_timer.timeout.connect(self._on_physics_timer)
        self._phys_clock = QElapsedTimer()
        self._pose_clock = QElapsedTimer()

    # -- state ---------------------------------------------------------------
    def request_state(self, state: State, tone: Tone = Tone.NORMAL) -> bool:
        accepted = self.machine.request(state, tone)
        self._refresh()
        return accepted

    def set_paused(self, paused: bool) -> None:
        self.machine.set_paused(paused)
        self._refresh()

    @property
    def roam(self) -> bool:
        return self._roam

    def set_roam(self, roam: bool) -> None:
        self._roam = roam
        if not roam:
            self._wanderer.hold()
        self._refresh()

    @property
    def mode(self) -> Mode:
        return self._physics.mode

    @property
    def floor_y(self) -> int:
        return round(self._physics.world.floor_y)

    def _roaming_active(self) -> bool:
        return (
            self._roam
            and not self._reduced
            and self.machine.state is State.IDLE
            and self._press_global is None
            and self._physics.mode is Mode.GROUNDED
        )

    def _pose(self) -> Pose | None:
        if self._physics.mode is Mode.GROUNDED:
            return None
        clock = self._pose_clock.elapsed() / 1000 if self._pose_clock.isValid() else 0.0
        return Pose(self._physics.mode, clock, self._physics.sway)

    def current_spec(self) -> FrameSpec:
        walk = None
        if self._wanderer.walking and self._roaming_active():
            walk = Walk(self._wanderer.facing, self._wanderer.steps)
        return frame_spec(
            self.machine.state,
            self.machine.ticks,
            reduced_motion=self._reduced,
            walk=walk,
            pose=self._pose(),
        )

    def _on_tick(self) -> None:
        self.machine.tick()
        if self._roaming_active():
            self._sync_bounds()
            self._wanderer.tick()
            if self._wanderer.x != self.x():
                self._physics.stand_at(self._wanderer.x)
                self.move(round(self._physics.x), round(self._physics.y))
        else:
            self._wanderer.hold()
        self._refresh()

    def _refresh(self) -> None:
        key = (self.current_spec(), self.machine.tone)
        if key != self._last:  # repaint only when the picture actually changes
            self._last = key
            self.update()

    # -- placement and the world --------------------------------------------
    def work_area(self) -> QRect:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _world(self) -> World:
        area = self.work_area()
        return World(
            area.left(),
            area.right() - self.width() + 1,
            area.top(),
            bottom_ground_y(area, self.height()),
        )

    def _sync_bounds(self) -> None:
        world = self._world()
        self._wanderer.set_bounds(round(world.lo_x), round(world.hi_x))
        self._physics.set_world(world)

    def place(self, pos: QPoint, settle: bool = False) -> None:
        """Put the duck at pos. Above the floor it falls; settle=True stands it on the floor."""
        self.move(pos)
        self._physics = Physics(pos.x(), pos.y(), self._world())
        if settle:
            self._physics.snap_to_floor()
        self.move(round(self._physics.x), round(self._physics.y))
        self._wanderer.set_bounds(round(self._physics.world.lo_x), round(self._physics.world.hi_x))
        self._wanderer.place(round(self._physics.x))
        if self._physics.airborne and self._reduced:
            self._physics.snap_to_floor()
            self.move(round(self._physics.x), round(self._physics.y))
        elif self._physics.airborne:
            self._begin_physics()
        self._refresh()

    def default_position(self) -> QPoint:
        area = QApplication.primaryScreen().availableGeometry()
        # Bottom-left: desktop notifications appear bottom-right and would cover the duck.
        return QPoint(area.left() + DEFAULT_MARGIN, bottom_ground_y(area, self.height()))

    # -- physics -------------------------------------------------------------
    def _begin_physics(self) -> None:
        self._phys_clock.start()
        if not self._pose_clock.isValid():
            self._pose_clock.start()
        if not self._phys_timer.isActive():
            self._phys_timer.start()

    def _on_physics_timer(self) -> None:
        self.physics_step(self._phys_clock.restart() / 1000.0)

    def physics_step(self, dt: float) -> None:
        """Advance the fall by dt seconds (public so tests can drive time exactly)."""
        self._physics.set_world(self._world())
        self._physics.step(dt)
        self.move(round(self._physics.x), round(self._physics.y))
        if self._physics.mode is Mode.GROUNDED:
            self._phys_timer.stop()
            self._pose_clock.invalidate()
            self._on_landed()
        self._refresh()

    def _on_landed(self) -> None:
        self._wanderer.place(round(self._physics.x))
        if self._notify_landing:
            self._notify_landing = False
            self.moved_by_user.emit(self.pos())

    def _begin_hold(self, now: float) -> None:
        self._physics.set_world(self._world())
        self._physics.x, self._physics.y = self.x(), self.y()  # trust where the window really is
        self._physics.grab(now)
        self._wanderer.hold()
        self._pose_clock.start()
        self._begin_physics()

    # -- painting ------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_bounds()
        self._tick_timer.start()
        if x11.session_type() == "x11":
            # The WM only accepts state changes once the window is mapped.
            QTimer.singleShot(250, self._apply_desktop_layer)
        else:
            log.warning(
                "not an X11 session; desktop-layer placement unavailable",
                extra={"session": x11.session_type()},
            )

    def _apply_desktop_layer(self) -> None:
        self._wmctrl_ok = x11.request_desktop_layer(int(self.winId()))
        QTimer.singleShot(VERIFY_DELAY_MS, self._verify_placement)

    def _verify_placement(self) -> None:
        result = x11.read_placement(int(self.winId()))
        method = "wmctrl+xprop" if self._wmctrl_ok else "qt-flags"
        self.placement = x11.PlacementResult(result.ok, method, result.states, result.message)
        log.info(
            "desktop placement",
            extra={
                "ok": result.ok,
                "method": method,
                "detail": result.message,  # not "message": that key is reserved by LogRecord
                "states": sorted(result.states),
            },
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        spec = self.current_spec()
        pixmap = render_frame(spec.name, self.machine.tone, self._scale, spec.flip)
        painter = QPainter(self)
        painter.drawPixmap(
            (PAD_X_CELLS + spec.dx) * self._scale, (PAD_TOP_CELLS + spec.dy) * self._scale, pixmap
        )
        painter.end()

    # -- mouse: drag, throw, click, double-click, context menu ---------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._dragging = False
        elif event.button() == Qt.MouseButton.RightButton and self._menu is not None:
            self._click_timer.stop()
            self._wanderer.pause(MENU_PAUSE_TICKS)
            self._menu.popup(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= DRAG_THRESHOLD_PX:
            self._dragging = True
            self._click_timer.stop()
            self._begin_hold(time.monotonic())
        if self._dragging:
            target = self._press_origin + delta
            self._physics.set_world(self._world())  # the pointer may have crossed to another screen
            self._physics.move_to(target.x(), target.y(), time.monotonic())
            self.move(round(self._physics.x), round(self._physics.y))
            self._refresh()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        was_drag = self._dragging
        self._press_global = None
        self._dragging = False
        if was_drag:
            self._let_go(time.monotonic())
        else:
            self._wanderer.pause(CLICK_PAUSE_TICKS)
            # Wait out the double-click interval so a double-click is not also a click.
            self._click_timer.start(QApplication.doubleClickInterval())

    def _let_go(self, now: float) -> None:
        self._notify_landing = True
        self._physics.release(now)
        if self._reduced and self._physics.airborne:
            self._physics.snap_to_floor()  # no fall animation: just stand on the floor
        self.move(round(self._physics.x), round(self._physics.y))
        if self._physics.mode is Mode.GROUNDED:
            self._phys_timer.stop()
            self._pose_clock.invalidate()
            self._on_landed()
        else:
            self._begin_physics()
        self._refresh()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit()
