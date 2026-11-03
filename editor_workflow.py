"""Reusable rooms, roster discovery, and validated downloads for the local UI."""

from __future__ import annotations

import mimetypes
import secrets
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any, cast

from generate import build
from seating import ROOM_SETTINGS, check_keys, config_from_dict, load_settings, parse_roster

ROOT = Path(__file__).resolve().parent
NEARBY_ROSTER = ROOT.parent / "list.csv"
DEFAULT_LIBRARY = ROOT / "examples" / "rooms.toml"
MAX_UPLOAD = 1024 * 1024
ROOM_LAYOUT_KEYS = (ROOM_SETTINGS - {"student_count", "section"}) | {"name"}


def room_library(raw: Any) -> dict[str, Any]:
    """Validate physical rooms without retaining exam counts or section choices."""
    if not isinstance(raw, dict):
        raise ValueError("A room library must be an object/table containing rooms.")
    if "course" in raw:
        config_from_dict(raw)  # Also accept a complete, valid exam config.
    else:
        check_keys(raw, {"version", "room_defaults", "rooms"}, "room library")
        if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
            raise ValueError("Room library version must be 1.")
    rooms = raw.get("rooms")
    defaults = raw.get("room_defaults", {})
    if not isinstance(rooms, list) or not rooms or not isinstance(defaults, dict):
        raise ValueError("A room library needs a nonempty rooms list and valid room_defaults.")
    check_keys(defaults, ROOM_SETTINGS, "room_defaults")
    prepared = []
    for room in rooms:
        if not isinstance(room, dict):
            raise ValueError("Every library room must be an object/table.")
        check_keys(room, ROOM_SETTINGS | {"name"}, "room")
        prepared.append(
            {**{k: v for k, v in room.items() if k in ROOM_LAYOUT_KEYS}, "student_count": 0}
        )
    config = config_from_dict(
        {
            "course": "Rooms",
            "exam_title": "Room library",
            "date": "2000-01-01",
            "time": "00:00",
            "seed": "rooms",
            "rooms": prepared,
            "room_defaults": {k: v for k, v in defaults.items() if k in ROOM_LAYOUT_KEYS},
        }
    )
    layouts = []
    for room in config.rooms:
        layout = {k: v for k, v in asdict(room).items() if k in ROOM_LAYOUT_KEYS}
        layout["aisles_after"] = list(room.aisles_after)
        layout["blocked_seats"] = list(room.blocked_seats)
        layouts.append(layout)
    return {"version": 1, "rooms": layouts}


def parse_room_library(content: str, config_format: str) -> dict[str, Any]:
    return room_library(load_settings(content, config_format))


def roster_summary(content: str, filename: str = "roster.csv") -> dict[str, Any]:
    students = parse_roster(content, filename)
    first = next(
        (line.strip() for line in content.lstrip("\ufeff").splitlines() if line.strip()), ""
    )
    return {
        "filename": filename,
        "students": len(students),
        "sections": dict(Counter(s.section or "No section" for s in students)),
        "format": "section_list" if first.startswith("Section:") else "table",
    }


def nearby_roster(load: bool = False) -> dict[str, Any]:
    found = NEARBY_ROSTER.is_file()
    result: dict[str, Any] = {"found": found, "filename": "list.csv"}
    if load:
        if not found:
            raise ValueError(
                "No list.csv was found in the parent folder. Choose a roster CSV instead."
            )
        if NEARBY_ROSTER.stat().st_size > MAX_UPLOAD:
            raise ValueError("Choose a roster smaller than 1 MB.")
        content = NEARBY_ROSTER.read_text(encoding="utf-8-sig")
        result.update(roster_summary(content, "list.csv"), content=content)
    return result


def workflow_action(route: str, body: dict[str, Any]) -> dict[str, Any]:
    if route == "/api/nearby-roster":
        return nearby_roster(load=True)
    if route == "/api/roster":
        content, filename = body.get("content"), body.get("filename", "roster.csv")
        if not isinstance(content, str) or not isinstance(filename, str):
            raise ValueError("Choose a CSV roster file.")
        if len(content.encode("utf-8")) > MAX_UPLOAD:
            raise ValueError("Choose a roster smaller than 1 MB.")
        return roster_summary(content, filename)
    if route == "/api/library/import":
        content, config_format = body.get("content"), body.get("format")
        if not isinstance(content, str) or not isinstance(config_format, str):
            raise ValueError("Choose a JSON or TOML room library or exam config.")
        return parse_room_library(content, config_format)
    if route == "/api/library/save":
        return room_library({"rooms": body.get("rooms")})
    raise ValueError("Unknown editor action.")


@dataclass(frozen=True)
class GeneratedExam:
    report: dict[str, Any]
    files: dict[str, bytes]


def generate_exam(body: dict[str, Any]) -> GeneratedExam:
    data = body.get("config")
    config = config_from_dict(data)
    raw = cast(dict[str, Any], data)
    roster = body.get("roster")
    if not isinstance(roster, str) or not roster.strip():
        raise ValueError("Load a roster CSV before generating PDFs.")
    saved = body.get("assignments", "")
    fresh = body.get("fresh", False)
    if not isinstance(saved, str) or type(fresh) is not bool:
        raise ValueError("Saved assignments must be CSV text; fresh must be true or false.")
    if fresh and saved:
        raise ValueError("Choose saved assignments or a new allocation, not both.")
    if raw.get("reuse_assignments_file") and not saved and not fresh:
        raise ValueError(
            "This config reuses saved assignments. Upload that CSV or choose 'Make a new allocation'."
        )
    source = dict(raw)
    if fresh:
        source.pop("reuse_assignments_file", None)
    elif saved and not source.get("reuse_assignments_file"):
        source["reuse_assignments_file"] = "saved_assignments.csv"
    with tempfile.TemporaryDirectory(prefix="exam-web-") as directory:
        root = Path(directory)
        roster_path = root / "roster.csv"
        roster_path.write_text(roster, encoding="utf-8")
        saved_path = None
        if saved:
            saved_path = root / "assignments.csv"
            saved_path.write_text(saved, encoding="utf-8")
        result = build(config, source, roster_path, root / "exam", saved_path)
        files = {p.name: p.read_bytes() for p in (root / "exam").iterdir()}
    return GeneratedExam({k: v for k, v in result.items() if k != "output"}, files)


class DownloadStore:
    """Keep at most five successful runs in memory for one hour per server."""

    def __init__(self) -> None:
        self.runs: dict[str, tuple[float, GeneratedExam]] = {}
        self.lock = Lock()

    def _prune(self) -> None:
        now = time.monotonic()
        self.runs = {key: run for key, run in self.runs.items() if now - run[0] < 3600}
        while len(self.runs) > 5:
            del self.runs[next(iter(self.runs))]

    def add(self, exam: GeneratedExam) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.runs[token] = (time.monotonic(), exam)
            self._prune()
        return {
            "report": exam.report,
            "files": [
                {"name": name, "url": f"/downloads/{token}/{name}"} for name in sorted(exam.files)
            ],
        }

    def get(self, token: str, filename: str) -> tuple[bytes, str] | None:
        with self.lock:
            self._prune()
            run = self.runs.get(token)
            if run is None or filename not in run[1].files:
                return None
            return run[1].files[filename], mimetypes.guess_type(filename)[
                0
            ] or "application/octet-stream"
