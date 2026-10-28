import subprocess
from collections.abc import Sequence

from sentinel_ai.platform import x11
from sentinel_ai.platform.motion import desktop_animations_disabled, resolve_reduced_motion

XPROP_OK = (
    "_NET_WM_STATE(ATOM) = _NET_WM_STATE_STICKY, _NET_WM_STATE_BELOW, "
    "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER\n"
)


def fake_runner(results: dict[str, tuple[int, str, str]]):
    calls: list[list[str]] = []

    def run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        rc, out, err = results.get(cmd[0], (127, "", f"{cmd[0]}: not found"))
        return subprocess.CompletedProcess(list(cmd), rc, out, err)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_session_type() -> None:
    assert x11.session_type({"XDG_SESSION_TYPE": "x11"}) == "x11"
    assert x11.session_type({"XDG_SESSION_TYPE": "Wayland"}) == "wayland"
    assert x11.session_type({"XDG_SESSION_TYPE": "tty"}) == "unknown"
    assert x11.session_type({}) == "unknown"


def test_parse_net_wm_state() -> None:
    assert "_NET_WM_STATE_BELOW" in x11.parse_net_wm_state(XPROP_OK)
    assert x11.parse_net_wm_state("_NET_WM_STATE:  not found.") == frozenset()
    assert x11.parse_net_wm_state("") == frozenset()


def test_apply_reports_verified_placement() -> None:
    run = fake_runner({"wmctrl": (0, "", ""), "xprop": (0, XPROP_OK, "")})
    result = x11.apply_desktop_layer(0x1234, run)
    assert result.ok and result.method == "wmctrl+xprop"
    wmctrl_calls = [c for c in run.calls if c[0] == "wmctrl"]
    assert all(c[:4] == ["wmctrl", "-i", "-r", "0x1234"] for c in wmctrl_calls)
    requested = {p for c in wmctrl_calls for p in c[-1].split(",")[1:]}
    assert requested == {"below", "skip_taskbar", "skip_pager", "sticky"}


def test_each_wmctrl_call_carries_at_most_two_properties() -> None:
    """Regression: wmctrl silently drops properties beyond the second, so STICKY was lost."""
    run = fake_runner({"wmctrl": (0, "", ""), "xprop": (0, XPROP_OK, "")})
    x11.request_desktop_layer(1, run)
    for call in run.calls:
        action, *props = call[-1].split(",")
        assert action == "add" and 1 <= len(props) <= 2


def test_apply_is_honest_when_wm_did_not_apply_states() -> None:
    run = fake_runner(
        {"wmctrl": (0, "", ""), "xprop": (0, "_NET_WM_STATE(ATOM) = _NET_WM_STATE_STICKY\n", "")}
    )
    result = x11.apply_desktop_layer(1, run)
    assert not result.ok
    assert "_NET_WM_STATE_BELOW" in result.message


def test_missing_wmctrl_falls_back_to_qt_flags_but_still_verifies() -> None:
    run = fake_runner({"xprop": (0, XPROP_OK, "")})
    result = x11.apply_desktop_layer(1, run)
    assert result.method == "qt-flags"
    assert result.ok  # Qt's own BELOW hint was enough, and we checked


def test_missing_xprop_is_never_reported_ok() -> None:
    result = x11.apply_desktop_layer(1, fake_runner({}))
    assert not result.ok and result.states == frozenset()


def test_animations_setting() -> None:
    assert desktop_animations_disabled(fake_runner({"gsettings": (0, "false\n", "")})) is True
    assert desktop_animations_disabled(fake_runner({"gsettings": (0, "true\n", "")})) is False
    assert desktop_animations_disabled(fake_runner({"gsettings": (0, "weird\n", "")})) is None
    assert desktop_animations_disabled(fake_runner({})) is None


def test_reduced_motion_precedence() -> None:
    off = fake_runner({"gsettings": (0, "false\n", "")})  # desktop says: animations off
    on = fake_runner({"gsettings": (0, "true\n", "")})
    assert resolve_reduced_motion(False, {"SENTINEL_REDUCED_MOTION": "1"}, off) is False
    assert resolve_reduced_motion(True, {}, on) is True
    assert resolve_reduced_motion(None, {"SENTINEL_REDUCED_MOTION": "1"}, on) is True
    assert resolve_reduced_motion(None, {"SENTINEL_REDUCED_MOTION": "0"}, off) is False
    assert resolve_reduced_motion(None, {}, off) is True
    assert resolve_reduced_motion(None, {}, on) is False
    assert resolve_reduced_motion(None, {}, fake_runner({})) is False  # unknown: motion allowed
