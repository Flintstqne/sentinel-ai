"""Regenerate every image and sample report used by the README. Fully offline and headless.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/make_docs_assets.py

Nothing here captures a real desktop or scans the machine it runs on. The scans come from a
made-up "sample-host" described by an in-memory Reader (documentation-reserved IP addresses,
invented user names), run through the same checks the app uses, so the screenshots show real
product output for a host that does not exist. A before/after pair shows findings resolving.
Animated GIFs need ffmpeg; the HTML screenshot needs chromium.
"""

from __future__ import annotations

import fnmatch
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from sentinel_ai.companion.animations import FrameSpec, Pose, Walk, frame_spec  # noqa: E402
from sentinel_ai.companion.physics import Mode, Physics, World  # noqa: E402
from sentinel_ai.companion.render import PAD_TOP_CELLS, PAD_X_CELLS, canvas_size, render_frame  # noqa: E402
from sentinel_ai.companion.roaming import Facing, Wanderer  # noqa: E402
from sentinel_ai.companion.state_machine import State, Tone  # noqa: E402
from sentinel_ai.monitoring import export  # noqa: E402
from sentinel_ai.monitoring.checks import CHECKS  # noqa: E402
from sentinel_ai.monitoring.model import Finding, Result  # noqa: E402
from sentinel_ai.monitoring.reader import CommandResult  # noqa: E402
from sentinel_ai.monitoring.scanner import run_check  # noqa: E402
from sentinel_ai.monitoring.store import ScanRecord, Store  # noqa: E402
from sentinel_ai.ui.dashboard import DashboardWindow  # noqa: E402
from sentinel_ai.ui.fonts import apply_app_font, make_font  # noqa: E402
from sentinel_ai.ui.icons import status_pixmap  # noqa: E402
from sentinel_ai.ui.panels import StatusPanel  # noqa: E402
from sentinel_ai.ui.theme import PALETTE, SPRITE_COLORS, build_stylesheet  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
SAMPLE = ROOT / "docs" / "sample"
HOST = "sample-host"
NOW = datetime.now().astimezone().replace(microsecond=0)
BG = QColor(PALETTE.bg)
SCALE = 3


# -- the sample host ------------------------------------------------------------------


class SampleReader:
    """An in-memory machine implementing the Reader protocol."""

    def __init__(
        self,
        files: dict[str, str],
        commands: dict[str, tuple[int, str]],
        mtimes: dict[str, datetime] | None = None,
    ) -> None:
        self.files, self.commands, self.mtimes = files, commands, mtimes or {}

    def read_text(self, path: str) -> str:
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(2, "No such file")

    def read_tail(self, path: str, max_bytes: int = 0) -> str:
        return self.read_text(path)

    def exists(self, path: str) -> bool:
        return path in self.files

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatch(p, pattern))

    def mtime(self, path: str) -> datetime | None:
        return self.mtimes.get(path)

    def run(self, argv: Sequence[str]) -> CommandResult:
        found = self.commands.get(" ".join(argv))
        if found is None:
            return CommandResult(127, "", f"{argv[0]}: not found")
        return CommandResult(found[0], found[1], "")

    def now(self) -> datetime:
        return NOW


def _auth_log() -> str:
    """Failed SSH logins from documentation addresses (RFC 5737), one with a hostile name."""
    users = [
        "admin",
        "<img src=x onerror=alert(1)>",
        "oracle",
        "test",
        "ubuntu",
        "postgres",
        "root",
    ]
    lines = []
    for i in range(31):
        t = (NOW - timedelta(minutes=17 * i + 3)).isoformat(timespec="microseconds")
        who = users[i % len(users)]
        ip = f"203.0.113.{10 + i % 9}"
        lines.append(
            f"{t} {HOST} sshd[{4100 + i}]: Failed password for invalid user {who} "
            f"from {ip} port {40000 + i} ssh2"
        )
    lines.append(
        f"{NOW.isoformat(timespec='microseconds')} {HOST} CRON[9]: session opened for user root"
    )
    return "\n".join(lines) + "\n"


def misconfigured() -> SampleReader:
    ss = (
        'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",812,3))\n'
        "tcp LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
        'tcp LISTEN 0 200 0.0.0.0:5432 0.0.0.0:* users:(("postgres",931,6))\n'
        "udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n"
    )
    ss_tcp = (  # `ss -tlnH` has no leading protocol column
        "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
        "LISTEN 0 200 0.0.0.0:5432 0.0.0.0:*\n"
    )
    updates = (
        "9 updates can be applied immediately.\n3 of these updates are standard security updates.\n"
    )
    return SampleReader(
        {
            "/etc/ufw/ufw.conf": "ENABLED=no\nLOGLEVEL=low\n",
            "/etc/default/ufw": 'DEFAULT_INPUT_POLICY="DROP"\n',
            "/usr/sbin/sshd": "",
            "/etc/ssh/sshd_config": "Include /etc/ssh/sshd_config.d/*.conf\n"
            "PasswordAuthentication yes\nPermitRootLogin prohibit-password\n",
            "/var/log/auth.log": _auth_log(),
            "/var/lib/update-notifier/updates-available": updates,
            "/etc/machine-id": "0" * 32,
        },
        {
            "systemctl is-active ufw": (0, "active\n"),
            "ufw status": (1, ""),
            "systemctl is-active ssh": (0, "active\n"),
            "systemctl is-active ssh.socket": (3, "inactive\n"),
            "ss -tlnH": (0, ss_tcp),
            "ss -tulnHp": (0, ss),
        },
        # The update summary has not been refreshed for nine days: too old to trust.
        mtimes={"/var/lib/update-notifier/updates-available": NOW - timedelta(days=9)},
    )


def hardened() -> SampleReader:
    ss = "tcp LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\nudp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n"
    ss_tcp = "LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
    return SampleReader(
        {
            "/etc/ufw/ufw.conf": "ENABLED=yes\nLOGLEVEL=low\n",
            "/etc/default/ufw": 'DEFAULT_INPUT_POLICY="DROP"\n',
            "/usr/sbin/sshd": "",
            "/etc/ssh/sshd_config": "PasswordAuthentication no\nPermitRootLogin no\n",
            "/var/log/auth.log": f"{NOW.isoformat()} {HOST} CRON[9]: session opened for root\n",
            "/var/lib/update-notifier/updates-available": "0 updates can be applied immediately.\n",
            "/etc/machine-id": "0" * 32,
        },
        {
            "systemctl is-active ufw": (0, "active\n"),
            "ufw status": (1, ""),
            "systemctl is-active ssh": (3, "inactive\n"),
            "systemctl is-active ssh.socket": (3, "inactive\n"),
            "ss -tlnH": (0, ss_tcp),
            "ss -tulnHp": (0, ss),
        },
        mtimes={"/var/lib/update-notifier/updates-available": NOW - timedelta(hours=3)},
    )


def scan_pair(
    store: Store,
) -> tuple[ScanRecord, tuple[Finding, ...], ScanRecord, tuple[Finding, ...]]:
    """Scan the misconfigured host three hours ago, then the hardened one now.

    Returns each scan together with the findings as they stood right after it, so a report
    for the first scan shows its findings open (not already resolved by the second).
    """
    before, after = misconfigured(), hardened()
    t1 = datetime.now(UTC) - timedelta(hours=3)
    rec1 = store.record_scan(HOST, tuple(run_check(c, before) for c in CHECKS), t1, t1)
    findings1 = store.all_findings(HOST)
    t2 = datetime.now(UTC)
    rec2 = store.record_scan(HOST, tuple(run_check(c, after) for c in CHECKS), t2, t2)
    return rec1, findings1, rec2, store.all_findings(HOST)


# -- drawing helpers ------------------------------------------------------------------


def stage(w: int, h: int, fill: QColor = BG) -> QImage:
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(fill)
    return image


def draw_sprite(p: QPainter, spec: FrameSpec, x: int, y: int, tone: Tone = Tone.NORMAL) -> None:
    """Paint a sprite frame as the companion window would, with its top-left at (x, y)."""
    pixmap = render_frame(spec.name, tone, SCALE, spec.flip)
    p.drawPixmap(x + (PAD_X_CELLS + spec.dx) * SCALE, y + (PAD_TOP_CELLS + spec.dy) * SCALE, pixmap)


def label(p: QPainter, text: str, cx: int, y: int, role: str = "small", muted: bool = True) -> None:
    p.setFont(make_font(role))
    p.setPen(QColor(PALETTE.text_muted if muted else PALETTE.text))
    p.drawText(QRect(cx - 90, y, 180, 20), Qt.AlignmentFlag.AlignHCenter, text)


def save(image: QImage, name: str) -> None:
    path = IMAGES / name
    image.save(str(path))
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


# -- images ---------------------------------------------------------------------------


def sheet(items: list[tuple[str, bool, Tone, str]], columns: int, scale: int, name: str) -> None:
    """A labelled grid of sprite frames at an integer scale."""
    cw, ch = canvas_size(scale)
    cell_w, cell_h = cw + 70, ch + 52
    rows = -(-len(items) // columns)
    image = stage(cell_w * columns, cell_h * rows)
    p = QPainter(image)
    for i, (frame, flip, tone, caption) in enumerate(items):
        col, row = i % columns, i // columns
        x0, y0 = col * cell_w, row * cell_h
        pixmap = render_frame(frame, tone, scale, flip)
        p.drawPixmap(
            x0 + (cell_w - cw) // 2 + PAD_X_CELLS * scale, y0 + 14 + PAD_TOP_CELLS * scale, pixmap
        )
        p.setFont(make_font("small"))
        p.setPen(QColor(PALETTE.text_muted))
        p.drawText(QRect(x0, y0 + 14 + ch + 8, cell_w, 22), Qt.AlignmentFlag.AlignHCenter, caption)
    p.end()
    save(image, name)


def sprite_sheets() -> None:
    n = Tone.NORMAL
    sheet(
        [
            ("base", False, n, "idle"),
            ("blink", False, n, "blink"),
            ("scan3", False, n, "scanning"),
            ("think1", False, n, "thinking"),
            ("insight", False, n, "insight"),
            ("alert", False, Tone.WARNING, "alert: warning"),
            ("alert", False, Tone.CRITICAL, "alert: critical"),
            ("happy", False, n, "all clear"),
            ("sleep1", False, n, "paused"),
        ],
        5,
        5,
        "sprites-states.png",
    )
    sheet(
        [
            ("walk_stand", False, n, "walking"),
            ("walk_front", False, n, "walking (stride)"),
            ("walk_stand", True, n, "walking left"),
            ("held_c_a", False, n, "held: wings up"),
            ("held_c_b", False, n, "held: wings out"),
            ("held_l_a", False, n, "held: legs trail"),
            ("fall_a", False, n, "falling"),
            ("land", False, n, "landing"),
        ],
        4,
        5,
        "sprites-poses.png",
    )


def write_gif(frames: list[QImage], name: str, fps: int) -> None:
    if not shutil.which("ffmpeg"):
        print("  (ffmpeg not found: skipping", name, ")")
        return
    with tempfile.TemporaryDirectory() as tmp:
        for i, frame in enumerate(frames):
            frame.save(f"{tmp}/f_{i:03d}.png")
        palette = "split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-i",
                f"{tmp}/f_%03d.png",
                "-vf",
                palette,
                str(IMAGES / name),
            ],
            check=True,
        )
    print(f"  docs/images/{name}  {(IMAGES / name).stat().st_size // 1024} KB")


def _stroll(seed: int, bounds: tuple[int, int], ticks: int) -> list[tuple[Facing, int, int, bool]]:
    """Snapshots of a seeded wanderer over time (the docs skip its initial stand)."""
    wanderer = Wanderer(bounds[0] + 100, bounds[0], bounds[1], SCALE, random.Random(seed))
    wanderer._stand_left = 3  # documentation only: begin strolling straight away
    out = []
    for _ in range(ticks):
        wanderer.tick()
        out.append((wanderer.facing, wanderer.x, wanderer.steps, wanderer.walking))
    return out


def walk_gif() -> None:
    w, h, floor = 520, 170, 150
    width, height = canvas_size(SCALE)
    bounds = (20, w - width - 20)

    # the seed that spends the most time walking, in both directions, pausing to face the viewer
    def walking_ticks(seed: int) -> int:
        run = _stroll(seed, bounds, 72)
        if {f for f, *_ in run} != {Facing.LEFT, Facing.RIGHT, Facing.FRONT}:
            return -1
        return sum(1 for *_, walking in run if walking)

    seed = max(range(3000), key=walking_ticks)
    frames = []
    for tick, (facing, x, steps, walking) in enumerate(_stroll(seed, bounds, 72)):
        image = stage(w, h)
        p = QPainter(image)
        p.fillRect(0, floor, w, h - floor, QColor(PALETTE.surface))
        p.fillRect(0, floor, w, 1, QColor(PALETTE.border_strong))
        spec = frame_spec(State.IDLE, tick, walk=Walk(facing, steps) if walking else None)
        draw_sprite(p, spec, x, floor - height)
        p.end()
        frames.append(image)
    write_gif(frames, "walk.gif", 8)


def physics_gif() -> None:
    w, h, floor_px = 520, 300, 280
    width, height = canvas_size(SCALE)
    world = World(0, w - width, 0, floor_px - height)
    body = Physics(70, world.floor_y, world)
    fps, frames, hold_started = 30, [], None
    path = [(70, world.floor_y), (150, 120), (260, 40), (330, 70)]  # a lift and an arc
    for n in range(int(4.4 * fps)):
        t = n / fps
        if 0.5 <= t < 1.9:  # carried along the path
            if hold_started is None:
                hold_started = t
                body.grab(t)
            u = (t - 0.5) / 1.4
            seg = min(int(u * 3), 2)
            f = u * 3 - seg
            (x0, y0), (x1, y1) = path[seg], path[seg + 1]
            body.move_to(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, t)
        elif abs(t - 1.9) < 1 / fps / 2 and body.mode is Mode.HELD:
            body.release(t)
        if body.mode is not Mode.HELD:
            body.step(1 / fps)
        pose = None
        if body.mode is not Mode.GROUNDED:
            clock = t - (hold_started or t)
            pose = Pose(body.mode, clock, body.sway)
        spec = frame_spec(State.IDLE, n // 4, pose=pose)
        image = stage(w, h)
        p = QPainter(image)
        p.fillRect(0, floor_px, w, h - floor_px, QColor(PALETTE.surface))
        p.fillRect(0, floor_px, w, 1, QColor(PALETTE.border_strong))
        draw_sprite(p, spec, round(body.x), round(body.y))
        if body.mode is Mode.HELD:  # the pointer, a small pixel arrow over the head
            px, py = round(body.x) + width // 2, round(body.y) - 6
            p.setPen(QColor(PALETTE.text))
            p.setBrush(QColor(PALETTE.text))
            p.drawPolygon([QPoint(px, py + 2), QPoint(px - 7, py - 12), QPoint(px + 7, py - 12)])
        label(
            p,
            {"held": "held", "falling": "falling", "landing": "landing"}.get(body.mode.value, ""),
            60,
            6,
            "small",
        )
        p.end()
        frames.append(image)
    write_gif(frames, "physics.gif", fps)


def widget_image(widget: QWidget, name: str) -> QImage:
    widget.show()
    QApplication.processEvents()
    image = widget.grab().toImage()
    save(image, name)
    return image


def dashboards(rec1: ScanRecord, rec2: ScanRecord) -> None:
    dash = DashboardWindow()
    dash.resize(1080, 700)
    dash.show_record(rec1)
    dash.select("firewall")
    widget_image(dash, "dashboard-before.png")
    dash.resize(1080, 1020)  # tall enough to show the whole evidence list
    dash.select("auth_failures")
    widget_image(dash, "dashboard-evidence.png")
    dash.select("ssh_hardening")
    widget_image(dash, "dashboard-recommendation.png")
    dash.resize(1080, 700)
    dash.show_record(rec2)
    widget_image(dash, "dashboard-after.png")
    empty = DashboardWindow()
    empty.resize(1080, 700)
    widget_image(empty, "dashboard-empty.png")


def popup_and_desktop(rec1: ScanRecord) -> None:
    panel = StatusPanel()
    panel.update_status(State.ALERT, False, rec1)
    panel.show()
    QApplication.processEvents()
    popup = panel.grab().toImage()
    save(popup, "status-popup.png")
    width, height = canvas_size(SCALE)
    w, h = 640, 420
    desk = stage(w, h, QColor("#0e1116"))
    p = QPainter(desk)
    p.fillRect(0, h - 40, w, 40, QColor(PALETTE.surface))
    p.fillRect(0, h - 41, w, 1, QColor(PALETTE.border_strong))
    for i in range(5):  # stand-ins for tray icons
        p.fillRect(w - 200 + i * 28, h - 28, 14, 14, QColor(PALETTE.border_strong))
    p.fillRect(w - 52, h - 28, 36, 14, QColor(PALETTE.border))
    duck_x, duck_y = 150, h - 40 - height
    p.drawImage(duck_x - popup.width() // 2 + width // 2, duck_y - popup.height() - 8, popup)
    draw_sprite(p, FrameSpec("alert", dx=0), duck_x, duck_y, Tone.CRITICAL)
    p.end()
    save(desk, "desktop-mockup.png")


def palette_image() -> None:
    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

    def contrast(a: str, b: str) -> float:
        la, lb = sorted((lum(QColor(a)), lum(QColor(b))), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    swatches = [
        ("bg", PALETTE.bg),
        ("surface", PALETTE.surface),
        ("raised", PALETTE.surface_raised),
        ("text", PALETTE.text),
        ("muted", PALETTE.text_muted),
        ("accent", PALETTE.accent),
        ("good", PALETTE.good),
        ("warning", PALETTE.warning),
        ("danger", PALETTE.danger),
    ]
    img = stage(9 * 112 + 24, 330)
    p = QPainter(img)
    for i, (name, hex_) in enumerate(swatches):
        x = 12 + i * 112
        p.fillRect(x, 14, 100, 70, QColor(hex_))
        p.setPen(QColor(PALETTE.border_strong))
        p.drawRect(x, 14, 100, 70)
        label(p, name, x + 50, 92, "body_strong", muted=False)
        label(p, hex_, x + 50, 112, "mono")
        if name not in ("bg", "surface", "raised"):
            label(p, f"{contrast(hex_, PALETTE.surface):.1f}:1 on card", x + 50, 132, "caption")
    for i, result in enumerate(Result):
        x = 40 + i * 190
        pm = status_pixmap(result, 5)
        p.drawPixmap(x, 190, pm)
        label(p, result.value, x + 22, 244, "small", muted=False)
    p.setFont(make_font("small"))
    p.setPen(QColor(PALETTE.text_muted))
    p.drawText(
        QRect(0, 282, img.width(), 22),
        Qt.AlignmentFlag.AlignHCenter,
        "status icons: each has its own shape as well as its own colour",
    )
    p.end()
    save(img, "design-palette.png")
    print(
        "  contrast check:",
        ", ".join(f"{n} {contrast(h, PALETTE.surface):.2f}" for n, h in swatches[3:]),
    )


def _content_bottom(image: QImage) -> int:
    """The last row that is not blank white (a headless screenshot is padded to the window)."""
    white = QColor("white").rgb()
    for y in range(image.height() - 1, 0, -1):
        if any(image.pixel(x, y) != white for x in range(0, image.width(), 7)):
            return y + 24
    return image.height()


def _shoot(html: Path, out: Path) -> QImage | None:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        print("  (chromium not found: skipping the HTML screenshots)")
        return None
    subprocess.run(
        [
            chromium,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=980,5200",
            f"--screenshot={out}",
            f"file://{html}",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    image = QImage(str(out))
    out.unlink()
    return image


def reports(
    rec1: ScanRecord,
    findings1: tuple[Finding, ...],
    rec2: ScanRecord,
    findings2: tuple[Finding, ...],
) -> None:
    for tag, record, findings in (("before", rec1, findings1), ("after", rec2, findings2)):
        report = export.build_report(
            record, findings, generated_at=datetime.now(UTC), privileged=False
        )
        (SAMPLE / f"sample-report-{tag}.html").write_text(
            export.render_html(report), encoding="utf-8"
        )
        (SAMPLE / f"sample-report-{tag}.json").write_text(
            export.render_json(report), encoding="utf-8"
        )
    print("  docs/sample/sample-report-{before,after}.{html,json}")
    before = _shoot(SAMPLE / "sample-report-before.html", IMAGES / "_tmp.png")
    after = _shoot(SAMPLE / "sample-report-after.html", IMAGES / "_tmp2.png")
    if before is not None:
        save(before.copy(0, 0, 980, 1230), "report-html-top.png")
        save(before.copy(0, 1130, 980, 1250), "report-html-evidence.png")
    if after is not None:
        bottom = _content_bottom(after)
        save(after.copy(0, bottom - 1100, 980, 1100), "report-html-history.png")


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    SAMPLE.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    apply_app_font(app)
    app.setStyleSheet(build_stylesheet())
    print("sprites")
    sprite_sheets()
    print("animations")
    walk_gif()
    physics_gif()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "sample.db")
        rec1, findings1, rec2, findings2 = scan_pair(store)
        print("dashboard")
        dashboards(rec1, rec2)
        print("popup and desktop mock-up")
        popup_and_desktop(rec1)
        print("design")
        palette_image()
        print("reports")
        reports(rec1, findings1, rec2, findings2)
        store.close()
    print("done; palette colours:", len(SPRITE_COLORS), "sprite colours")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
