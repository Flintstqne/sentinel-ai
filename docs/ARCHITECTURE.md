# Architecture

```
src/sentinel_ai/
  app.py                 wiring: companion + tray + panels, single-instance lock, main()
  config.py              Config dataclass, XDG paths, tolerant JSON load / atomic save
  logging_setup.py       JSON-lines logging (rotating file + stderr)
  companion/
    pixelart.py          shaded-ellipse and outline toolkit used to build the sprite
    state_machine.py     Qt-free behaviour: states, tones, priorities, pause
    roaming.py           Wanderer: seeded, Qt-free stand/walk/turn behaviour along the floor
    physics.py           Qt-free rigid body: held, thrown, falling, landing (explicit time steps)
    animations.py        pure (state, ticks, walk) -> FrameSpec(name, dx, dy, flip)
    sprites.py           front and side pixel data, frame table, flip (pure Python)
    render.py            sprite grid -> scaled QPixmap, tray icon
    window.py            frameless draggable window, click/double-click/menu, X11 hook
  monitoring/
    model.py             Result, Severity, Evidence, Assessment (no PASS without evidence), Finding
    reader.py            the only door to the system: read-only Reader protocol + LocalReader
    checks/              firewall, ssh, auth, updates, ports, and the CHECKS registry
    scanner.py           runs every check; a crash becomes ERROR, never a PASS
    store.py             SQLite: assessments (audit trail) and findings (lifecycle)
    reaction.py          pure: scan result -> companion state, tone, notifications
    report.py            plain-text evidence report
    summary.py           labels, worst-first order and the lead sentence (shared by UI and reports)
    export.py            audit evidence reports: one report dict, JSON and self-contained HTML
    monitor.py           Qt: runs scans on a worker thread and on a schedule
  platform/
    x11.py               session detection, wmctrl request + xprop verification
    motion.py            reduced-motion resolution
  ui/
    theme.py             design tokens, status palette, type scale, stylesheet (no fonts in QSS)
    fonts.py             loads the bundled OFL fonts, builds QFonts from the type roles
    icons.py             pixel-art status icons, logo, pixmap cache clearing
    format.py            Qt-free: relative and local time, sorting, headline, safe rich text
    dashboard.py         the dashboard window
    panels.py            the compact status popup
```

## Design decisions

**Logic is separated from Qt.** The state machine, animation table, sprite data, config
and X11 adapter import no Qt, so they are tested without a display. Qt behaviour (drag,
click disambiguation, repaint-on-change) is tested on the `offscreen` platform.

**Behaviour is a function of ticks, not wall-clock time.** The state machine advances in
fixed 125 ms ticks and animations are pure functions of `(state, ticks_in_state)`. That
makes every animation deterministic and testable, and the widget repaints only when the
visible frame changes, so an idle companion does almost no work.

**Transient states have priority rules.** ALERT > INSIGHT > HAPPY. A running transient is
never cut short by a lower or equal one; sustained requests during a transient only change
the state it returns to. A critical alert can upgrade a warning alert. While monitoring is
paused (SLEEPING) every request is refused.

**Roaming is a separate, seeded concern.** `Wanderer` owns where the duck is and which way it
faces; the state machine owns what it is *doing*. The window only lets the wanderer run when
the state is IDLE, nothing is pressed, roaming is on and reduced motion is off; any other
state stops it where it stands. The wanderer moves one sprite cell per tick, never leaves
its bounds (re-read from the current screen every tick, so a monitor change cannot strand
it), pauses at edges to turn, and keeps the side pose for the last step so the duck never
slides while facing the user. Facing left is the right-facing walk cycle mirrored.

**Picking the duck up is a physics problem, kept out of Qt.** `Physics` is a small rigid body
with four modes (grounded, held, falling, landing) and takes every time and position as an
argument, so it is deterministic and tested without a display or a clock. Falling uses exact
constant-gravity kinematics (average velocity over the step, and the contact speed from
`v^2 = v0^2 + 2gd`); an earlier semi-implicit Euler version was off by up to 13% on short falls
at coarse steps, which a test caught. The throw velocity comes from the last 100 ms of pointer
history and is zero if the pointer had stopped; the sides absorb most of the speed, and a hard
landing squashes while a hop does not.

The window runs two clocks: the 8 Hz behaviour tick, and a 60 Hz physics timer that exists only
while the duck is held or airborne, so a standing duck still costs almost nothing. The pose
(held, falling, landing) outranks whatever state the duck was in and picks a sprite frame from
wing-flap phase and how fast the pointer is moving. `moved_by_user` fires when the duck comes
to *rest*, not at release, so only the final horizontal position is saved. With reduced motion
there is no fall: letting go stands the duck on the floor.

**The window's bottom edge is the ground.** The canvas pads left, right and top but not
below the feet, and animation offsets only ever move the sprite sideways or up. Cinnamon
refuses to let a window overlap the panel, so any padding under the feet would be pushed up
and leave the duck floating above the panel (this was found on the live desktop).

**Qt objects are released before the QApplication.** Pixmap caches, widgets and the worker
pool are cleared in `SentinelApp.shutdown()` (and in the test session's teardown). A `QPixmap`
destroyed during interpreter shutdown, after the QApplication, crashes the process; this
showed up as a crash at exit in 8 of 9 test runs and is now guarded by a test that runs the
real `main()` in a child process.

**Reduced motion keeps meaning, drops movement.** Each state has one static frame and no
offsets, so state is still readable without animation.

**Honest placement.** `apply` and `verify` are separate steps. The result is `ok` only if
the window manager reports the required states, and the method used (`wmctrl+xprop` or
`qt-flags`) is logged.

**No fabricated data.** The status panel and dashboard report only facts the app has. Until
Phase 3 there are no assessments, and the UI says "No assessment has run yet".

## How monitoring works

Checks never touch the system directly; they receive a `Reader` (files, a glob, a command,
the clock). That keeps them read-only by construction and lets tests feed them missing
files, permission errors and conflicting sources. Each check returns an `Assessment`; the
scanner wraps every check so a crash becomes an ERROR (never a PASS) and the scan carries on.

`Store.record_scan` turns assessments into findings with a lifecycle driven purely by
evidence (see `CHECKS.md`). `react()` is a pure function from a finished scan to a companion
state, tone and notifications, and it responds to what changed so an already-known finding
does not re-alarm every hour. `Monitor` runs the scan on a worker thread (the callable opens
its own SQLite connection there) and delivers the result to the UI thread; a zero scan
interval means manual only and never arms the timer.

There is no AI layer, by decision: the available local models hallucinate, and an invented
finding or a confident wrong explanation is worse than a plain deterministic one in an audit
tool. Every explanation is written by the check that collected the evidence. The seam remains
(observations are kept apart from interpretation, and remediation is only ever text), so a
model could later be added as a clearly labelled, optional reader that cannot alter evidence.
