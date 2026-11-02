import csv
import json
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from pypdf import PdfReader

from generate import build, geometry, register_fonts, write_assignments
from seating import (
    Config,
    Room,
    Student,
    active_students,
    assign,
    column_label,
    parse_seat,
    read_config,
    read_roster,
    reuse_assignments,
    select_seats,
    validate_assignments,
)


def people(count: int) -> list[Student]:
    result = [
        Student(f"{i + 1:08d}", f"Student{i + 1}", f"Surname{i + 1}", "1") for i in range(count)
    ]
    if result:
        result[0] = Student("00000001", "Çağrı İhsan", "Öztürk", "1")
    return result


def config(rooms: list[Room], exclusions: tuple[str, ...] = ()) -> Config:
    return Config(
        "EEE 212",
        "Midterm Exam",
        date(2026, 11, 4),
        time(19, 30),
        "test-seed",
        "EEE212",
        tuple(rooms),
        "turkish",
        tuple(exclusions),
    )


def roster_csv(path: Path, students: list[Student]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("student_id", "given_name", "surname", "section"))
        writer.writerows((s.student_id, s.given_name, s.surname, s.section) for s in students)


class InputTests(unittest.TestCase):
    def test_unicode_course_accepts_explicit_filename_prefix(self) -> None:
        raw: dict[str, Any] = {
            "course": "ÖĞÜ",
            "exam_title": "Exam",
            "date": "2026-11-04",
            "time": "19:30",
            "seed": 0,
            "filename_prefix": "COURSE",
            "rooms": [{"name": "A", "rows": 2, "columns": 2, "student_count": 2}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw))
            cfg, _ = read_config(path)
            self.assertEqual(cfg.filename_prefix, "COURSE")

    def test_standard_roster_preserves_leading_zeros_and_turkish_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "students.csv"
            roster_csv(path, people(3))
            self.assertEqual(read_roster(path), people(3))

    def test_legacy_section_roster_and_mixed_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "students.csv"
            path.write_text(
                "Section: 1\n1\tÖztürk\tÇağrı İhsan\t00000001\n"
                "2\tErginbaş    Alp Emre\t00000002\n\nSection: 2\n"
                "1\tGül\tİhsan Atalay\t00000003 \t\n",
                encoding="utf-8",
            )
            parsed = read_roster(path)
            self.assertEqual(len(parsed), 3)
            self.assertEqual(parsed[1], Student("00000002", "Alp Emre", "Erginbaş", "1"))
            self.assertEqual(parsed[2].section, "2")

    def test_duplicate_ids_rejected_even_if_names_differ(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "students.csv"
            roster_csv(path, [people(1)[0], Student("00000001", "Different", "Person")])
            with self.assertRaisesRegex(ValueError, "Duplicate student ID"):
                read_roster(path)

    def test_exclusions_and_total_must_reconcile(self) -> None:
        cfg = config([Room("A", 2, 2, 2)], ("00000003",))
        self.assertEqual(len(active_students(cfg, people(3))), 2)
        with self.assertRaisesRegex(ValueError, "sum to"):
            active_students(replace(cfg, excluded_student_ids=()), people(3))
        with self.assertRaisesRegex(ValueError, "not in the roster"):
            active_students(replace(cfg, excluded_student_ids=("unknown",)), people(3))

    def test_configuration_capacity_and_invalid_seat(self) -> None:
        raw: dict[str, Any] = {
            "course": "EEE 212",
            "exam_title": "Exam",
            "date": "2026-11-04",
            "time": "19:30",
            "seed": "x",
            "rooms": [
                {"name": "A", "rows": 2, "columns": 2, "student_count": 4, "blocked_seats": ["1A"]}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "exceed"):
                read_config(path)
            raw["rooms"][0].update(student_count=2, blocked_seats=["3A"])
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "outside"):
                read_config(path)
            raw["rooms"][0].update(blocked_seats=[], student_cout=2)
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "Unknown"):
                read_config(path)


class AllocationTests(unittest.TestCase):
    def test_frontmost_rows_and_partial_row_across_blocks(self) -> None:
        room = Room("EE03", 7, 6, 27, (2, 4))
        seats = select_seats(room, random.Random(9))
        self.assertEqual(
            [sum(parse_seat(s)[0] == row for s in seats) for row in range(1, 8)],
            [6, 6, 6, 6, 3, 0, 0],
        )
        self.assertEqual(
            {room.block(parse_seat(s)[1]) for s in seats if s.startswith("5")}, {0, 1, 2}
        )

    def test_blocked_front_seats_and_unequal_room_counts(self) -> None:
        rooms = [Room("A", 3, 4, 7, (2,), ("1A",)), Room("B", 4, 3, 5)]
        cfg = config(rooms)
        result = assign(cfg, people(12))
        validate_assignments(cfg, people(12), result)
        self.assertNotIn(("A", "1A"), {(a.room, a.seat) for a in result})
        self.assertEqual(sum(a.room == "A" for a in result), 7)
        self.assertEqual(sum(a.room == "B" for a in result), 5)

    def test_seed_reproducibility_and_input_row_order(self) -> None:
        cfg = config([Room("A", 7, 6, 27), Room("B", 7, 6, 27)])
        result = assign(cfg, people(54))
        self.assertEqual(result, assign(cfg, list(reversed(people(54)))))
        self.assertNotEqual(result, assign(replace(cfg, seed="another-seed"), people(54)))

    def test_random_and_spread_do_not_use_blocked_or_duplicate_seats(self) -> None:
        for policy in ("spread", "random"):
            with self.subTest(policy=policy):
                room = Room("A", 7, 6, 27, (2, 4), ("1A", "7F"), policy)
                seats = select_seats(room, random.Random(12))
                self.assertEqual(len(seats), 27)
                self.assertEqual(len(set(seats)), 27)
                self.assertFalse(set(seats) & set(room.blocked_seats))
                if policy == "spread":
                    loads = [sum(parse_seat(s)[0] == row for s in seats) for row in range(1, 8)]
                    self.assertLessEqual(max(loads) - min(loads), 1)

    def test_saved_assignments_reprint_and_mismatches(self) -> None:
        cfg = config([Room("A", 3, 4, 8)])
        students = people(8)
        result = assign(cfg, students)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.csv"
            write_assignments(path, cfg, result)
            restored = reuse_assignments(replace(cfg, seed="irrelevant"), students, path)
            self.assertEqual(set(result), set(restored))
            changed = [replace(students[0], given_name="Wrong"), *students[1:]]
            with self.assertRaisesRegex(ValueError, "does not match"):
                reuse_assignments(cfg, changed, path)
            broken = [result[0], replace(result[1], seat=result[0].seat), *result[2:]]
            with self.assertRaisesRegex(ValueError, "share"):
                validate_assignments(cfg, students, broken)

    def test_multiletter_columns(self) -> None:
        self.assertEqual(column_label(26), "AA")
        self.assertEqual(parse_seat("12AB"), (12, 27))


class OutputTests(unittest.TestCase):
    def test_pdf_csv_zip_round_trip_for_mixed_grids(self) -> None:
        cfg = config(
            [Room("EE03", 3, 4, 7, (2,), ("1A",)), Room("EE04", 4, 3, 5, seating_policy="spread")]
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            roster = base / "students.csv"
            roster_csv(roster, people(12))
            report = build(cfg, {}, roster, base / "output")
            self.assertEqual(report["assigned_students"], 12)
            seat_pdf = PdfReader(base / "output/EEE212_Seating_Plans.pdf")
            self.assertEqual(len(seat_pdf.pages), 2)
            self.assertIn("Proctor Name:", seat_pdf.pages[0].extract_text())
            doc = PdfReader(base / "output/EEE212_Door_Lists.pdf")
            self.assertEqual(len(doc.pages), 2)
            for page, room in zip(doc.pages, cfg.rooms, strict=True):
                text = page.extract_text()
                self.assertIn(room.name, text)
                self.assertNotIn("No.", text)
                self.assertNotIn("ALPHABETICAL", text)
                self.assertIn("19:30", text)
            with ZipFile(base / "output/EEE212_Exam_Package.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("Validation_Report.json", archive.namelist())
            with self.assertRaisesRegex(ValueError, "already exists"):
                build(cfg, {}, roster, base / "output")
            build(cfg, {}, roster, base / "output", overwrite=True)
            self.assertEqual(len(list(base.glob("output.backup-*"))), 1)

    def test_large_room_uses_a3_and_paginated_door_list(self) -> None:
        cfg = config([Room("Large", 10, 8, 65, (4,))])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            roster = base / "students.csv"
            roster_csv(roster, people(65))
            report = build(cfg, {}, roster, base / "output")
            self.assertEqual(report["seating_paper_sizes"]["Large"], "A3")
            self.assertEqual(report["door_list_pages"]["Large"], 3)
            self.assertEqual(len(PdfReader(base / "output/EEE212_Door_Lists.pdf").pages), 3)

    def test_bad_counts_do_not_create_partial_output(self) -> None:
        cfg = config([Room("A", 2, 2, 2)])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            roster = base / "students.csv"
            roster_csv(roster, people(3))
            with self.assertRaisesRegex(ValueError, "sum to"):
                build(cfg, {}, roster, base / "output")
            self.assertFalse((base / "output").exists())

    def test_unreadable_forced_grid_is_rejected(self) -> None:
        register_fonts()
        room = Room("Dense", 10, 8, 10, paper_size="A4")
        with self.assertRaisesRegex(ValueError, "no readable"):
            geometry(room, [])


if __name__ == "__main__":
    unittest.main()
