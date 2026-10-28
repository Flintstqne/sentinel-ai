import json
from pathlib import Path

from sentinel_ai.config import MAX_SCALE, MIN_SCALE, Config, config_dir, state_dir


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert Config.load(tmp_path / "nope.json") == Config()


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.json"
    cfg = Config(
        companion_x=120,
        companion_y=-5,
        scale=6,
        reduced_motion=True,
        monitoring_paused=True,
        roam=False,
    )
    cfg.save(path)
    assert Config.load(path) == cfg
    assert not path.with_suffix(".json.tmp").exists()


def test_corrupt_file_is_set_aside_and_defaults_used(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert Config.load(path) == Config()
    assert path.with_suffix(".json.corrupt").exists()


def test_non_object_json_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert Config.load(path) == Config()


def test_bad_types_are_ignored_not_trusted(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "companion_x": "12",
                "companion_y": True,  # bool is an int in Python; must be rejected
                "scale": "big",
                "reduced_motion": "yes",
                "monitoring_paused": 1,
                "unknown_key": "ignored",
            }
        ),
        encoding="utf-8",
    )
    assert Config.load(path) == Config()


def test_scale_is_clamped(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scale": 99}), encoding="utf-8")
    assert Config.load(path).scale == MAX_SCALE
    path.write_text(json.dumps({"scale": -3}), encoding="utf-8")
    assert Config.load(path).scale == MIN_SCALE


def test_reduced_motion_none_means_follow_desktop(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"reduced_motion": None}), encoding="utf-8")
    assert Config.load(path).reduced_motion is None


def test_xdg_paths() -> None:
    assert config_dir({"XDG_CONFIG_HOME": "/x"}) == Path("/x/sentinel-ai")
    assert state_dir({"XDG_STATE_HOME": "/y"}) == Path("/y/sentinel-ai")
    assert config_dir({}).parts[-2:] == (".config", "sentinel-ai")


def test_roam_defaults_on_and_roundtrips(tmp_path: Path) -> None:
    assert Config().roam is True
    path = tmp_path / "config.json"
    Config(roam=False).save(path)
    assert Config.load(path).roam is False


def test_non_bool_roam_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"roam": "no"}), encoding="utf-8")
    assert Config.load(path).roam is True


def test_the_test_suite_is_isolated_from_the_real_user_directories() -> None:
    """Regression: tests once wrote their failure tracebacks into the real log file."""
    from sentinel_ai.config import data_dir

    home = Path.home()
    for directory in (config_dir(), state_dir(), data_dir()):
        assert home not in directory.parents or "xdg" in str(directory), directory
        assert "xdg" in str(directory), f"{directory} is not an isolated test directory"
