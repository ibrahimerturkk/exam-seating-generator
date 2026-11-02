"""Configuration compatibility, defaults, and actionable input errors."""

import json
from pathlib import Path
from typing import Any

import pytest

from seating import read_config

ROOT = Path(__file__).resolve().parents[1]


def settings() -> dict[str, Any]:
    return {
        "course": "EEE 212",
        "exam_title": "Exam",
        "date": "2026-11-04",
        "time": "19:30",
        "seed": 0,
        "room_defaults": {"rows": 3, "columns": 4, "student_count": 7, "aisles_after": [2]},
        "rooms": [{"name": "A"}, {"name": "B", "student_count": 5}],
    }


def load(tmp_path: Path, raw: dict[str, Any]) -> Any:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return read_config(path)[0]


@pytest.mark.parametrize("example", ["current_exam", "mixed_rooms", "config_template"])
def test_toml_and_legacy_json_examples_are_equivalent(example: str) -> None:
    toml, _ = read_config(ROOT / f"examples/{example}.toml")
    legacy, _ = read_config(ROOT / f"examples/{example}.json")
    assert toml == legacy


def test_defaults_and_override_precedence(tmp_path: Path) -> None:
    raw = settings()
    raw.update(seating_policy="spread", paper_size="A3")
    raw["room_defaults"].update(seating_policy="random", blocked_seats=["1A"])
    raw["rooms"][1].update(seating_policy="front_first", blocked_seats=[], aisles_after=[])
    cfg = load(tmp_path, raw)
    assert cfg.filename_prefix == "EEE212"
    assert cfg.seed == "0"
    first, second = cfg.rooms
    assert (first.rows, first.columns, first.student_count) == (3, 4, 7)
    assert first.blocked_seats == ("1A",)
    assert second.blocked_seats == ()
    assert second.aisles_after == ()
    assert (first.seating_policy, second.seating_policy) == ("random", "front_first")
    assert first.paper_size == second.paper_size == "A3"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("course", "", "course"),
        ("exam_title", None, "exam_title"),
        ("date", "20261104", "YYYY-MM-DD"),
        ("date", "2026-02-30", "day"),
        ("time", "7:30", "HH:MM"),
        ("time", "25:00", "hour"),
        ("seed", True, "seed"),
        ("seed", "", "seed"),
        ("sort_order", "wrong", "sort_order"),
        ("seating_policy", "wrong", "seating_policy"),
        ("paper_size", "A5", "paper_size"),
        ("roster_file", [], "roster_file"),
        ("reuse_assignments_file", "", "reuse_assignments_file"),
        ("rooms", [], "rooms"),
        ("rooms", ["A"], "room"),
        ("room_defaults", [], "room_defaults"),
        ("room_defaults", {"row": 3}, "Unknown"),
        ("excluded_student_ids", [1], "excluded"),
        ("excluded_student_ids", ["a", "a"], "excluded"),
        ("filename_prefix", "../file", "filename"),
        ("filename_prefix", False, "filename"),
        ("unexpected", 1, "Unknown"),
    ],
)
def test_invalid_exam_settings(tmp_path: Path, key: str, value: Any, message: str) -> None:
    raw = settings()
    raw[key] = value
    with pytest.raises(ValueError, match=message):
        load(tmp_path, raw)


@pytest.mark.parametrize("key", ["course", "exam_title", "date", "time", "seed", "rooms"])
def test_required_exam_settings(tmp_path: Path, key: str) -> None:
    raw = settings()
    del raw[key]
    with pytest.raises(ValueError, match=f"Missing configuration setting: {key}"):
        load(tmp_path, raw)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("name", " ", "name"),
        ("rows", True, "integer"),
        ("rows", 0, "integer"),
        ("rows", 101, "limit"),
        ("columns", 53, "limit"),
        ("student_count", -1, "integer"),
        ("student_count", 13, "exceed"),
        ("aisles_after", "2", "aisles_after"),
        ("aisles_after", [2, 2], "aisles_after"),
        ("aisles_after", [True], "aisles_after"),
        ("aisles_after", [4], "aisles_after"),
        ("blocked_seats", "1A", "blocked_seats"),
        ("blocked_seats", [1], "blocked_seats"),
        ("blocked_seats", ["1a"], "Invalid seat"),
        ("blocked_seats", ["4A"], "outside"),
        ("blocked_seats", ["1E"], "outside"),
        ("blocked_seats", ["1A", "1A"], "duplicate"),
        ("seating_policy", "wrong", "seating_policy"),
        ("paper_size", "A5", "paper_size"),
        ("student_cout", 7, "Unknown"),
    ],
)
def test_invalid_room_settings(tmp_path: Path, key: str, value: Any, message: str) -> None:
    raw = settings()
    raw["rooms"][0][key] = value
    with pytest.raises(ValueError, match=message):
        load(tmp_path, raw)


def test_defaults_are_validated_even_when_overridden(tmp_path: Path) -> None:
    raw = settings()
    raw["room_defaults"]["rows"] = False
    for room in raw["rooms"]:
        room["rows"] = 3
    with pytest.raises(ValueError, match="room_defaults.rows"):
        load(tmp_path, raw)


def test_required_room_settings_and_unique_names(tmp_path: Path) -> None:
    raw = settings()
    raw["rooms"][1]["name"] = " A "
    with pytest.raises(ValueError, match="unique"):
        load(tmp_path, raw)
    raw["rooms"][1]["name"] = "B"
    del raw["room_defaults"]["rows"]
    with pytest.raises(ValueError, match="Missing room setting: rows"):
        load(tmp_path, raw)


@pytest.mark.parametrize(
    ("suffix", "content", "message"),
    [
        ("json", "[]", "object/table"),
        ("json", '{"course":"A","course":"B"}', "Duplicate"),
        ("json", "{", "property name"),
        ("toml", 'course = "A"\ncourse = "B"', "overwrite"),
        ("yaml", "course: A", "toml or .json"),
    ],
)
def test_malformed_config_files(tmp_path: Path, suffix: str, content: str, message: str) -> None:
    path = tmp_path / f"config.{suffix}"
    path.write_text(content)
    with pytest.raises(ValueError, match=message):
        read_config(path)
