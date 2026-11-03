"""Roster parsing, configuration validation, and reproducible seat assignment."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def clean(value: object) -> str:
    return unicodedata.normalize("NFC", " ".join(str(value).split()))


def filename(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_-]", "", value)
    if not result:
        raise ValueError(f"Cannot make a filename from {value!r}; use letters/numbers.")
    return result


def column_label(index: int) -> str:
    """Zero-based index -> A, B, ..., Z, AA, AB, ... ."""
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def parse_seat(code: str) -> tuple[int, int]:
    match = re.fullmatch(r"([1-9][0-9]*)([A-Z]+)", code)
    if not match:
        raise ValueError(f"Invalid seat {code!r}; expected a code such as 3B.")
    col = 0
    for char in match[2]:
        col = col * 26 + ord(char) - 64
    return int(match[1]), col - 1


def seat_code(row: int, col: int) -> str:
    return f"{row}{column_label(col)}"


@dataclass(frozen=True)
class Student:
    student_id: str
    given_name: str
    surname: str
    section: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.surname}"


@dataclass(frozen=True)
class Room:
    name: str
    rows: int
    columns: int
    student_count: int
    aisles_after: tuple[int, ...] = ()
    blocked_seats: tuple[str, ...] = ()
    seating_policy: str = "front_first"
    paper_size: str = "auto"
    section: str = ""

    @property
    def available_seats(self) -> list[str]:
        blocked = set(self.blocked_seats)
        return [
            seat_code(r, c)
            for r in range(1, self.rows + 1)
            for c in range(self.columns)
            if seat_code(r, c) not in blocked
        ]

    def block(self, col: int) -> int:
        return sum(col >= boundary for boundary in self.aisles_after)


@dataclass(frozen=True)
class Config:
    course: str
    exam_title: str
    date: date
    time: time
    seed: str
    filename_prefix: str
    rooms: tuple[Room, ...]
    sort_order: str = "turkish"
    excluded_student_ids: tuple[str, ...] = ()
    section_policy: str = "mixed"


@dataclass(frozen=True)
class Assignment:
    student: Student
    room: str
    seat: str


def integer(value: object, label: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}.")
    return value


def check_keys(data: dict, allowed: set[str], label: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown {label} setting(s): {', '.join(sorted(unknown))}")


ROOM_SETTINGS = {
    "rows",
    "columns",
    "student_count",
    "aisles_after",
    "blocked_seats",
    "seating_policy",
    "paper_size",
    "section",
}


def json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Do not silently accept a misspelled copy/paste that repeats a setting."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration setting: {key}")
        result[key] = value
    return result


def validate_room_settings(item: dict[str, Any], label: str) -> None:
    """Validate defaults even when every room overrides a particular value."""
    for key in ("rows", "columns", "student_count"):
        if key in item:
            integer(item[key], f"{label}.{key}", 0 if key == "student_count" else 1)
    for key, limit in (("rows", 100), ("columns", 52)):
        if key in item and item[key] > limit:
            raise ValueError(f"{label}: supported grid limit is 100 rows and 52 columns.")
    if "aisles_after" in item:
        aisles = item["aisles_after"]
        if (
            not isinstance(aisles, list)
            or any(type(v) is not int or v < 1 for v in aisles)
            or len(set(aisles)) != len(aisles)
        ):
            raise ValueError(f"{label}.aisles_after must contain unique positive column numbers.")
    if "blocked_seats" in item:
        blocked = item["blocked_seats"]
        if not isinstance(blocked, list) or any(not isinstance(v, str) for v in blocked):
            raise ValueError(f"{label}.blocked_seats must be a list of seat codes.")
        for code in blocked:
            parse_seat(code)
        if len(set(blocked)) != len(blocked):
            raise ValueError(f"{label}: duplicate blocked seat.")
    if "seating_policy" in item and item["seating_policy"] not in (
        "front_first",
        "spread",
        "random",
    ):
        raise ValueError(f"{label}: seating_policy must be front_first, spread, or random.")
    if "paper_size" in item and item["paper_size"] not in ("auto", "A4", "A3", "A2"):
        raise ValueError(f"{label}: paper_size must be auto, A4, A3, or A2.")
    if "section" in item and not isinstance(item["section"], str):
        raise ValueError(f"{label}.section must be a string.")


def read_config(path: Path) -> tuple[Config, dict[str, Any]]:
    return parse_config(path.read_text(encoding="utf-8-sig"), path.suffix)


def parse_config(content: str, config_format: str) -> tuple[Config, dict[str, Any]]:
    """Read uploaded or on-disk configuration using the same strict validation."""
    raw = load_settings(content, config_format)
    return config_from_dict(raw), raw


def load_settings(content: str, config_format: str) -> Any:
    """Decode JSON/TOML for exam configs and reusable room libraries."""
    content = content.lstrip("\ufeff")
    if config_format.lower().lstrip(".") == "toml":
        raw = tomllib.loads(content)
    elif config_format.lower().lstrip(".") == "json":
        raw = json.loads(content, object_pairs_hook=json_object)
    else:
        raise ValueError("Configuration filename must end in .toml or .json.")
    return raw


def config_from_dict(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be an object/table.")
    check_keys(
        raw,
        {
            "course",
            "exam_title",
            "date",
            "time",
            "seed",
            "filename_prefix",
            "rooms",
            "seating_policy",
            "paper_size",
            "sort_order",
            "excluded_student_ids",
            "roster_file",
            "reuse_assignments_file",
            "room_defaults",
            "section_policy",
        },
        "exam",
    )
    for key in ("course", "exam_title", "date", "time", "seed", "rooms"):
        if key not in raw:
            raise ValueError(f"Missing configuration setting: {key}")
    section_policy = raw.get("section_policy", "mixed")
    if section_policy not in ("mixed", "separate_rooms", "keep_together"):
        raise ValueError("section_policy must be mixed, separate_rooms, or keep_together.")
    for key in ("course", "exam_title", "date", "time"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(f"{key} must be a nonempty string.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["date"]):
        raise ValueError("date must be YYYY-MM-DD (quote it in TOML).")
    exam_date = date.fromisoformat(raw["date"])
    if not re.fullmatch(r"\d{2}:\d{2}", raw["time"]):
        raise ValueError("time must be HH:MM in 24-hour format.")
    exam_time = time.fromisoformat(raw["time"])
    if type(raw["seed"]) not in (str, int) or not str(raw["seed"]):
        raise ValueError("seed must be a nonempty string or an integer.")
    sort_order = raw.get("sort_order", "turkish")
    if sort_order not in ("turkish", "unicode"):
        raise ValueError("sort_order must be 'turkish' or 'unicode'.")
    if raw.get("seating_policy", "front_first") not in ("front_first", "spread", "random"):
        raise ValueError("seating_policy must be front_first, spread, or random.")
    if raw.get("paper_size", "auto") not in ("auto", "A4", "A3", "A2"):
        raise ValueError("paper_size must be auto, A4, A3, or A2.")
    for key in ("roster_file", "reuse_assignments_file"):
        if key in raw and (not isinstance(raw[key], str) or not raw[key].strip()):
            raise ValueError(f"{key} must be a nonempty file path string.")
    if not isinstance(raw["rooms"], list) or not raw["rooms"]:
        raise ValueError("rooms must be a nonempty list.")
    defaults = raw.get("room_defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("room_defaults must be an object/table.")
    check_keys(defaults, ROOM_SETTINGS, "room_defaults")
    validate_room_settings(defaults, "room_defaults")
    rooms = []
    for item in raw["rooms"]:
        if not isinstance(item, dict):
            raise ValueError("Every room must be an object/table.")
        check_keys(item, {"name"} | ROOM_SETTINGS, "room")
        validate_room_settings(item, "room")
        item = {**defaults, **item}
        for key in ("name", "rows", "columns", "student_count"):
            if key not in item:
                raise ValueError(f"Missing room setting: {key}")
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise ValueError("Room name must be a nonempty string.")
        name = clean(item["name"])
        rows = integer(item["rows"], f"{name}.rows")
        columns = integer(item["columns"], f"{name}.columns")
        count = integer(item["student_count"], f"{name}.student_count", 0)
        aisles = item.get("aisles_after", [])
        if any(v >= columns for v in aisles):
            raise ValueError(
                f"{name}.aisles_after must contain unique column numbers from 1 to {columns - 1}."
            )
        blocked = item.get("blocked_seats", [])
        for code in blocked:
            r, c = parse_seat(code)
            if r > rows or c >= columns:
                raise ValueError(f"{name}: blocked seat {code} is outside the grid.")
        policy = cast(str, item.get("seating_policy", raw.get("seating_policy", "front_first")))
        paper = cast(str, item.get("paper_size", raw.get("paper_size", "auto")))
        section = clean(item.get("section", ""))
        if section and section_policy == "mixed":
            raise ValueError(f"{name}: choose a separate section policy to set a room's section.")
        room = Room(
            name,
            rows,
            columns,
            count,
            tuple(sorted(aisles)),
            tuple(blocked),
            policy,
            paper,
            section,
        )
        if count > len(room.available_seats):
            raise ValueError(
                f"{name}: {count} students exceed {len(room.available_seats)} available seats."
            )
        rooms.append(room)
    if len({r.name for r in rooms}) != len(rooms):
        raise ValueError("Room names must be unique.")
    excluded = raw.get("excluded_student_ids", [])
    if (
        not isinstance(excluded, list)
        or any(not isinstance(s, str) or not s.strip() for s in excluded)
        or len(set(excluded)) != len(excluded)
    ):
        raise ValueError("excluded_student_ids must be a list of unique, nonempty strings.")
    prefix = raw.get("filename_prefix")
    if prefix is None:
        prefix = filename(raw["course"])
    if not isinstance(prefix, str) or filename(prefix) != prefix:
        raise ValueError(
            "filename_prefix may contain only letters, numbers, hyphens, and underscores."
        )
    config = Config(
        clean(raw["course"]),
        clean(raw["exam_title"]),
        exam_date,
        exam_time,
        str(raw["seed"]),
        prefix,
        tuple(rooms),
        sort_order,
        tuple(excluded),
        section_policy,
    )
    return config


def header_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    return {
        "id": "student_id",
        "studentid": "student_id",
        "studentno": "student_id",
        "givenname": "given_name",
        "firstname": "given_name",
        "name": "given_name",
        "lastname": "surname",
        "surname": "surname",
        "section": "section",
        "room": "room",
        "seat": "seat",
        "seatcode": "seat",
    }.get(key, key)


def csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    return parse_csv(path.read_text(encoding="utf-8-sig"), path.name)


def parse_csv(content: str, label: str) -> tuple[list[str], list[dict[str, str]]]:
    # Detect the delimiter from the header, so padding and irregular blank rows
    # cannot affect detection. Do not strip the actual stream: tabs can delimit
    # empty fields, and quoted values can contain newlines.
    first = next((line for line in content.splitlines() if line.strip()), "")
    try:
        delimiter = csv.Sniffer().sniff(first.rstrip(" \t"), delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    # Header-only sniffing cannot infer escaped quotes in later data rows.
    # Use standard CSV quoting independently of the detected delimiter.
    reader = csv.reader(io.StringIO(content), delimiter=delimiter, skipinitialspace=True)
    headers: list[str] | None = None
    records = []
    for row in reader:
        if not any(value.strip() for value in row):
            continue
        if delimiter == "\t":
            # Extra trailing tabs are whitespace padding, provided they do not
            # remove an expected field or discard any nonempty cell.
            minimum = len(headers) if headers is not None else 0
            while len(row) > minimum and not row[-1].strip():
                row.pop()
        if headers is None:
            headers = [header_key(value.strip()) for value in row]
            if len(set(headers)) != len(headers):
                raise ValueError(f"Duplicate column header in {label}.")
            continue
        if len(row) != len(headers):
            raise ValueError(f"{label}, row {reader.line_num}: wrong number of columns.")
        records.append({key: clean(value) for key, value in zip(headers, row, strict=True)})
    return headers or [], records


def read_roster(path: Path) -> list[Student]:
    return parse_roster(path.read_text(encoding="utf-8-sig"), path.name)


def parse_roster(content: str, label: str = "roster.csv") -> list[Student]:
    content = content.lstrip("\ufeff")
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    students = []
    if first.startswith("Section:"):
        section = ""
        for index, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*Section:\s*(\S+)\s*", line)
            if match:
                section = match[1]
                continue
            fields = [v.strip() for v in line.split("\t") if v.strip()]
            if len(fields) == 4 and fields[0].isdigit():
                _, surname, given, student_id = fields
            else:
                # Some section exports mix tabs and spaces within the name columns.
                parts = line.split()
                if len(parts) < 4 or not parts[0].isdigit():
                    raise ValueError(f"{label}, line {index}: unrecognized roster row.")
                surname, given, student_id = parts[1], " ".join(parts[2:-1]), parts[-1]
            students.append(Student(clean(student_id), clean(given), clean(surname), section))
    else:
        headers, records = parse_csv(content, label)
        required = {"student_id", "given_name", "surname"}
        if not required <= set(headers):
            raise ValueError(
                "Roster needs headers student_id,given_name,surname (section is optional)."
            )
        students = [
            Student(r["student_id"], r["given_name"], r["surname"], r.get("section", ""))
            for r in records
        ]
    if not students:
        raise ValueError("Roster is empty.")
    seen = set()
    for student in students:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", student.student_id):
            raise ValueError(f"Invalid student ID: {student.student_id!r}")
        if student.student_id in seen:
            raise ValueError(f"Duplicate student ID: {student.student_id}")
        if not student.given_name or not student.surname:
            raise ValueError(f"Missing name for student {student.student_id}.")
        seen.add(student.student_id)
    return students


def active_students(
    config: Config, students: list[Student], check_counts: bool = True
) -> list[Student]:
    unknown = set(config.excluded_student_ids) - {s.student_id for s in students}
    if unknown:
        raise ValueError(f"Excluded ID(s) are not in the roster: {', '.join(sorted(unknown))}")
    excluded = set(config.excluded_student_ids)
    result = [s for s in students if s.student_id not in excluded]
    expected = sum(r.student_count for r in config.rooms)
    if check_counts and len(result) != expected:
        raise ValueError(
            f"Active roster has {len(result)} students, but room student_count values sum to {expected}."
        )
    return result


def section_plan(
    config: Config, students: list[Student], flexible: bool = False
) -> list[tuple[str, int]]:
    """Assign whole rooms to sections; a section may occupy several rooms.

    Fixed plans respect every student_count. The editor's explicit auto-fit action
    instead uses physical capacities and returns counts that fit the roster.
    """
    if any(not s.section for s in students):
        raise ValueError(
            "Every student needs a section when keeping sections separate. Add a section column to the roster."
        )
    totals = Counter(s.section for s in students)
    names = sorted(totals)
    sizes = tuple(totals[name] for name in names)
    limits = [len(r.available_seats) if flexible else r.student_count for r in config.rooms]
    order = sorted(range(len(config.rooms)), key=lambda i: (-limits[i], i))
    for room, limit in zip(config.rooms, limits, strict=True):
        if limit and room.section and room.section not in totals:
            raise ValueError(f"{room.name}: section {room.section!r} is not in the active roster.")
    visited = 0

    @lru_cache(maxsize=100_000)
    def solve(position: int, remaining: tuple[int, ...]) -> tuple[tuple[str, int], ...] | None:
        nonlocal visited
        visited += 1
        if visited > 100_000:
            raise ValueError(
                "Section planning is too complex. Select a section for each room and try again."
            )
        if position == len(order):
            return () if not any(remaining) else None
        if sum(remaining) > sum(limits[i] for i in order[position:]):
            return None
        index = order[position]
        room, limit = config.rooms[index], limits[index]
        candidates = sorted(range(len(names)), key=lambda i: (-remaining[i], names[i]))
        # Sections with equal remaining sizes are interchangeable unless rooms
        # have explicit section choices.
        seen: set[int] = set()
        locked = any(r.section for r in config.rooms)
        for i in candidates:
            if room.section and room.section != names[i]:
                continue
            if not locked and remaining[i] in seen:
                continue
            seen.add(remaining[i])
            count = min(limit, remaining[i]) if flexible else limit
            if not count or count > remaining[i]:
                continue
            if config.section_policy == "keep_together" and (
                remaining[i] != sizes[i] or count != remaining[i]
            ):
                continue
            updated = list(remaining)
            updated[i] -= count
            rest = solve(position + 1, tuple(updated))
            if rest is not None:
                return ((names[i], count), *rest)
        if flexible or not limit:
            rest = solve(position + 1, remaining)
            if rest is not None:
                return ((room.section, 0), *rest)
        return None

    plan = solve(0, sizes)
    if plan is None:
        summary = ", ".join(f"{name}: {totals[name]}" for name in names)
        raise ValueError(
            f"Cannot keep sections separate with these room counts/capacities. Sections: {summary}. Adjust room counts, add rooms, or use auto-fit in the config editor."
        )
    result = [("", 0)] * len(config.rooms)
    for index, value in zip(order, plan, strict=True):
        result[index] = value
    return result


def suggest_rooms(config: Config, students: list[Student]) -> tuple[Room, ...]:
    """Auto-fit counts using real capacities, without silently changing a build."""
    students = active_students(config, students, check_counts=False)
    if config.section_policy != "mixed":
        return tuple(
            replace(room, student_count=count)
            for room, (_section, count) in zip(
                config.rooms, section_plan(config, students, True), strict=True
            )
        )
    counts = [0] * len(config.rooms)
    capacities = [len(r.available_seats) for r in config.rooms]
    for _ in students:
        available = [i for i, count in enumerate(counts) if count < capacities[i]]
        if not available:
            raise ValueError(
                "Not enough available seats for the roster. Add a room or enlarge a grid."
            )
        index = min(available, key=lambda i: (counts[i], i))
        counts[index] += 1
    return tuple(
        replace(room, student_count=count) for room, count in zip(config.rooms, counts, strict=True)
    )


def sort_key(student: Student, order: str = "turkish") -> tuple:
    def part(value: str) -> str | tuple[int, ...]:
        if order == "unicode":
            return value.casefold()
        value = value.replace("I", "ı").replace("İ", "i").casefold()
        alphabet = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        ranks = {c: i for i, c in enumerate(alphabet)}
        return tuple(ranks.get(c, ord(c) + 500) for c in value)

    return part(student.surname), part(student.given_name), student.student_id


def select_seats(room: Room, rng: random.Random) -> list[str]:
    available = room.available_seats
    needed = room.student_count
    selected: list[str] = []
    if room.seating_policy == "random":
        return rng.sample(available, needed)
    if room.seating_policy == "front_first":
        for row in range(1, room.rows + 1):
            candidates = [s for s in available if parse_seat(s)[0] == row]
            if len(candidates) <= needed:
                selected.extend(candidates)
                needed -= len(candidates)
            else:
                # Fill a partial row across the table blocks before doubling up.
                while needed:
                    loads = {room.block(parse_seat(s)[1]): 0 for s in candidates}
                    for s in selected:
                        r, c = parse_seat(s)
                        if r == row and room.block(c) in loads:
                            loads[room.block(c)] += 1
                    minimum = min(loads.values())
                    seat = rng.choice(
                        [s for s in candidates if loads[room.block(parse_seat(s)[1])] == minimum]
                    )
                    selected.append(seat)
                    candidates.remove(seat)
                    needed -= 1
            if not needed:
                break
    else:  # spread: balance occupied seats across available rows and table blocks.
        remaining = list(available)
        while len(selected) < needed:
            rows = {parse_seat(s)[0] for s in remaining}
            row_loads = {r: sum(parse_seat(s)[0] == r for s in selected) for r in rows}
            minimum = min(row_loads.values())
            row = rng.choice(sorted(r for r in rows if row_loads[r] == minimum))
            candidates = [s for s in remaining if parse_seat(s)[0] == row]
            loads = {room.block(parse_seat(s)[1]): 0 for s in candidates}
            for s in selected:
                r, c = parse_seat(s)
                if r == row and room.block(c) in loads:
                    loads[room.block(c)] += 1
            minimum = min(loads.values())
            seat = rng.choice(
                [s for s in candidates if loads[room.block(parse_seat(s)[1])] == minimum]
            )
            selected.append(seat)
            remaining.remove(seat)
    rng.shuffle(selected)
    return selected


def assign(config: Config, students: list[Student]) -> list[Assignment]:
    students = sorted(students, key=lambda s: s.student_id)
    random.Random(f"{config.seed}:rooms").shuffle(students)
    section_groups = {
        name: [s for s in students if s.section == name] for name in {s.section for s in students}
    }
    plan = section_plan(config, students) if (config.section_policy != "mixed") else []
    result: list[Assignment] = []
    offset = 0
    for index, room in enumerate(config.rooms):
        seed = hashlib.sha256(f"{config.seed}:seats:{room.name}".encode()).hexdigest()
        seats = select_seats(room, random.Random(seed))
        if config.section_policy != "mixed":
            name, count = plan[index]
            people = section_groups.get(name, [])[:count]
            section_groups[name] = section_groups.get(name, [])[count:]
        else:
            people = students[offset : offset + room.student_count]
        result.extend(Assignment(s, room.name, seat) for s, seat in zip(people, seats, strict=True))
        offset += room.student_count
    validate_assignments(config, students, result)
    return result


def reuse_assignments(config: Config, students: list[Student], path: Path) -> list[Assignment]:
    headers, records = csv_rows(path)
    if not {"student_id", "room", "seat"} <= set(headers):
        raise ValueError("Saved assignments need student_id,room,seat columns.")
    roster = {s.student_id: s for s in students}
    result = []
    for record in records:
        sid = record["student_id"]
        if sid not in roster:
            raise ValueError(f"Saved assignments contain an unknown/excluded student: {sid}")
        student = roster[sid]
        for key in ("given_name", "surname", "section"):
            if key in headers and record[key] != getattr(student, key):
                raise ValueError(f"Saved assignment {key} does not match the roster for {sid}.")
        result.append(Assignment(student, record["room"], record["seat"]))
    validate_assignments(config, students, result)
    return result


def validate_assignments(
    config: Config, students: list[Student], assignments: list[Assignment]
) -> None:
    roster = {s.student_id for s in students}
    ids = [a.student.student_id for a in assignments]
    if len(ids) != len(set(ids)):
        raise ValueError("A student has more than one assignment.")
    if set(ids) != roster:
        raise ValueError(
            f"Assignments do not cover the roster. Missing: {sorted(roster - set(ids))}; extra: {sorted(set(ids) - roster)}"
        )
    if len({(a.room, a.seat) for a in assignments}) != len(assignments):
        raise ValueError("Two students share the same room and seat.")
    rooms = {r.name: r for r in config.rooms}
    for a in assignments:
        if a.room not in rooms or a.seat not in rooms[a.room].available_seats:
            raise ValueError(f"Unavailable seat: {a.room} {a.seat} ({a.student.student_id})")
    for room in config.rooms:
        actual = sum(a.room == room.name for a in assignments)
        if actual != room.student_count:
            raise ValueError(
                f"{room.name}: expected {room.student_count} students; found {actual}."
            )
        if config.section_policy != "mixed":
            sections = {a.student.section for a in assignments if a.room == room.name}
            if "" in sections:
                raise ValueError(
                    f"{room.name}: every student needs a section when keeping sections separate."
                )
            if len(sections) > 1:
                raise ValueError(
                    f"{room.name}: students from different sections share a room. Generate a fresh allocation or choose the mixed section policy."
                )
            if sections and room.section and sections != {room.section}:
                raise ValueError(
                    f"{room.name}: assignments do not match the configured section {room.section!r}."
                )
    if config.section_policy == "keep_together":
        for section in {s.section for s in students}:
            occupied = {a.room for a in assignments if a.student.section == section}
            if len(occupied) > 1:
                raise ValueError(
                    f"Section {section!r} is split across rooms. Each section must stay together in one room."
                )
