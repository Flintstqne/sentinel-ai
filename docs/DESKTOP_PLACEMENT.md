# Desktop placement on Cinnamon (X11)

Goal: the duck sits above the desktop background, below normal windows, off the taskbar,
on every workspace, and still receives mouse input.

## Approach

1. Qt window flags: frameless, `Qt.Tool`, `WindowStaysOnBottomHint`,
   `WindowDoesNotAcceptFocus`, translucent background. Qt itself requests
   `_NET_WM_STATE_BELOW` from these.
2. After the window is mapped, ask the window manager for the remaining standard EWMH
   states through `wmctrl` (`platform/x11.py`). Plain EWMH states only: no private
   Muffin calls, and **no `_NET_WM_WINDOW_TYPE_DESKTOP`**, which would put the window in
   the desktop layer alongside Nemo's desktop window and risks it losing clicks.
3. Read the states back with `xprop` ~400 ms later and log what the WM actually applied.
   Placement is only reported `ok` when `BELOW`, `SKIP_TASKBAR` and `STICKY` are all present.

## Verified on this machine (Cinnamon 6.6.9, X11, 4 workspaces)

| Behaviour | Result | How it was checked |
|---|---|---|
| Stacking order | Desktop < duck < terminal | `_NET_CLIENT_LIST_STACKING` |
| Window states | `BELOW`, `SKIP_TASKBAR`, `SKIP_PAGER`, `STICKY` | `xprop`, logged `ok: true` |
| Visible on every workspace | yes | screenshot of the duck's region on workspace 2: 96 visor pixels with `STICKY`, 0 without |
| Single click | opens status panel, not the dashboard | `xdotool` click, app log |
| Double click | opens dashboard once, no extra single-click | `xdotool`, app log |
| Closing the dashboard | app keeps running | process check |
| Drag | moves window, saved to config, not counted as a click | `xdotool` drag, config file |
| Right click | context menu opens | new X windows appear |
| Restart | position restored | relaunch, `xdotool getwindowgeometry` |
| Single instance | second launch refused | log message |
| Tray | `Active` StatusNotifierItem owned by the app's pid | D-Bus query |
| Walking | idle duck strolled along the bottom: 52 distinct x positions in 40 s, constant y, inside the work area | position sampling with `xdotool` |
| Resting on the panel | window bottom edge equals the work-area bottom (y 968 + 72 = 1040) and is not pushed | `xdotool getwindowgeometry`, screenshots |
| Click on a walking duck | opens the status panel; the duck then stands still for 3 s | `xdotool`, app log |
| Drag while roaming | superseded: dropping the duck mid-air used to make that height its walking line; it now falls to the floor (see physics below) | |
| `roam: false` | no movement over 30 s | sampling |

## Pitfalls found while testing (all regression-tested)

* **`wmctrl -b` accepts at most two properties per call** and silently ignores the rest.
  The first version passed four, so `STICKY` was never applied. Requests are now split
  into two calls.
* **`wmctrl -lG` and the "desktop" column cannot show stickiness here** (always `0`), and
  `xwininfo` reports `IsViewable` on other workspaces whether or not the window is sticky.
  Only a screenshot of the window's region discriminates.
* `extra={"message": ...}` in a log call raises `KeyError` because `message` is reserved
  by `LogRecord`. This crashed the placement callback on the first live run.

* **Cinnamon will not let a window overlap the panel.** The first roaming build padded one
  transparent cell under the feet and placed the window so that cell overlapped the panel;
  Muffin pushed the window up by that cell and the duck floated above the panel. The canvas
  now has no padding below the feet (see `ARCHITECTURE.md`).

## Physics (pick up, throw, fall, land)

Verified on an **isolated virtual display (Xvfb, no window manager)** with real pointer events,
not on the live Cinnamon session: the live desktop had maximized windows covering the strip
where the duck stands, and the duck lives *behind* normal windows, so scripted pointer input
there would have gone to those windows instead. (A first attempt on the live desktop did exactly
that and delivered a click and drag to another application's window; the physics testing moved to Xvfb as a
result.)

| Behaviour | Result |
|---|---|
| Lift 400 px and carry 144 px sideways | window followed the pointer; held pose shown |
| Release mid-air | fell with the expected gravity curve (32 px after 147 ms; the model predicts 35) |
| Landing | rested exactly on the floor, x kept, and the app saved the rest position once |
| Fast flick and release | carried at about 2000 px/s, bounced off the screen side, landed and slid to a stop, never left the screen |
| Tracebacks | none |

Not verified on the real desktop: how Muffin treats the moving window during a drag or fall,
the 60 Hz timer's CPU cost there, and catching the duck mid-air by hand.

## Not verified / known limits

* **Wayland:** the adapter is X11-only. On a Wayland session the companion falls back to
  a normal frameless tool window and logs a warning; below-windows stacking is not
  guaranteed there.
* **Multi-monitor:** position clamping and per-screen walking bounds are unit-tested; neither was tried on a second monitor.
* **Walking toggle from the tray menu** is covered by an app-level test and the config key was verified live, but the menu item was not clicked live.
* **Reduced motion on the live desktop:** the preference resolution is unit-tested; the
  Cinnamon animations setting was not toggled live.
* **Tray menu items** were not clicked live (the same actions are wired to the companion's
  context menu, which opened).
* Without `wmctrl` installed, the app relies on the Qt flags alone and logs that fact.
