"""Protect student identity, CSV interoperability, and saved allocation checks."""

import csv
import io
from dataclasses import replace
from pathlib import Path

import pytest

from generate import write_assignments
from seating import (
    Assignment,
    Room,
    Student,
    assign,
    parse_roster,
    read_roster,
    reuse_assignments,
    sort_key,
    validate_assignments,
)
from tests.test_generator import config, people


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_alias_headers_delimiters_and_optional_sections(tmp_path: Path, delimiter: str) -> None:
    path = tmp_path / "roster.csv"
    path.write_text(
        delimiter.join(["Student No", "First Name", "Last Name"])
        + "\n"
        + delimiter.join(["00123", "C\u0327ag\u0306rı", "O\u0308ztu\u0308rk"])
        + "\n",
        encoding="utf-8-sig",
    )
    assert read_roster(path) == [Student("00123", "Çağrı", "Öztürk")]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_padded_fields_blank_lines_and_trailing_tabs_are_ignored(delimiter: str) -> None:
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([" student_id ", " given_name ", " surname ", " section "])
    writer.writerow([" 00123 ", " Çağrı İhsan ", " Öztürk, Kaya ", " 1 "])
    padded = " \t\r\n" + " \t\r\n".join(stream.getvalue().split("\r\n")) + " \t\r\n"
    assert parse_roster(padded) == [Student("00123", "Çağrı İhsan", "Öztürk, Kaya", "1")]


@pytest.mark.parametrize("padding", ["   ", "\t\t", " \t \u00a0 "])
def test_section_list_trailing_whitespace_preserves_students(padding: str) -> None:
    rows = [
        "Section: 1",
        "1\tÖztürk\tÇağrı İhsan\t00000001",
        "",
        "Section: 2",
        "1\tGül\tİhsan Atalay\t00000002",
    ]
    content = "\ufeff" + "\r\n".join(row + padding for row in rows)
    assert parse_roster(content) == [
        Student("00000001", "Çağrı İhsan", "Öztürk", "1"),
        Student("00000002", "İhsan Atalay", "Gül", "2"),
    ]


def test_whitespace_handling_preserves_multiline_quoted_fields_and_empty_section() -> None:
    content = 'student_id,given_name,surname,section\n00123,"Çağrı\nİhsan",Öztürk, \t\n \t\n'
    assert parse_roster(content) == [Student("00123", "Çağrı İhsan", "Öztürk")]
    tsv = "student_id\tgiven_name\tsurname\tsection\t \t\n00123\tÇağrı\tÖztürk\t\t \t\n"
    assert parse_roster(tsv) == [Student("00123", "Çağrı", "Öztürk")]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_padded_csv_preserves_escaped_quotes_and_multiline_names(delimiter: str) -> None:
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(["student_id", "given_name", "surname", "section"])
    writer.writerow(["00123", ' Çağrı "Example"\nİhsan ', " Öztürk, Kaya ", ""])
    assert parse_roster(" \t\n" + stream.getvalue() + " \t\n") == [
        Student("00123", 'Çağrı "Example" İhsan', "Öztürk, Kaya")
    ]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_whitespace_does_not_hide_missing_fields_or_extra_data(delimiter: str) -> None:
    header = delimiter.join(["student_id", "given_name", "surname", "section"])
    for values in (["001", "Name"], ["001", "Name", "Surname", "1", "extra"]):
        content = f"{header}\n \t\n{delimiter.join(values)} \t\n"
        with pytest.raises(ValueError, match="wrong number of columns"):
            parse_roster(content)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("student_id,given_name,surname\n", "empty"),
        ("id,given_name,surname\n001,A,B,extra", "number of columns"),
        ("id,given_name,surname\n001,A", "number of columns"),
        ("id,student_id,given_name,surname\n001,001,A,B", "Duplicate column"),
        ("id,given_name\n001,A", "needs headers"),
        ("id,given_name,surname\n../001,A,B", "Invalid student ID"),
        ("id,given_name,surname\n001,,B", "Missing name"),
        ("Section: 1\nnot a student", "unrecognized roster row"),
    ],
)
def test_malformed_rosters(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "roster.csv"
    path.write_text(content)
    with pytest.raises(ValueError, match=message):
        read_roster(path)


def test_turkish_surname_order_and_identity_tiebreak() -> None:
    surnames = ["Z", "Ü", "U", "Ş", "S", "Ö", "O", "İ", "I", "Ğ", "G", "Ç", "C"]
    students = [Student(str(i), "Name", name) for i, name in enumerate(surnames)]
    assert [s.surname for s in sorted(students, key=sort_key)] == list(reversed(surnames))
    assert sort_key(Student("01", "A", "B"), "unicode") < sort_key(
        Student("02", "A", "B"), "unicode"
    )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("duplicate-student", "more than one"),
        ("missing-student", "cover the roster"),
        ("unknown-room", "Unavailable seat"),
        ("blocked-seat", "Unavailable seat"),
        ("wrong-room-count", "expected"),
    ],
)
def test_assignment_validation_rejects_invalid_allocations(case: str, message: str) -> None:
    cfg = config([Room("A", 2, 2, 2, blocked_seats=("2B",)), Room("B", 2, 2, 1)])
    students = people(3)
    assignments = assign(cfg, students)
    if case == "duplicate-student":
        assignments.append(assignments[0])
    elif case == "missing-student":
        assignments.pop()
    elif case == "unknown-room":
        assignments[0] = replace(assignments[0], room="Unknown")
    elif case == "blocked-seat":
        assignments[0] = replace(assignments[0], room="A", seat="2B")
    else:
        assignments[0] = replace(assignments[0], room="B", seat="2A")
        assignments[-1] = replace(assignments[-1], room="B", seat="1A")
    with pytest.raises(ValueError, match=message):
        validate_assignments(cfg, students, assignments)


def test_saved_csv_requires_headers_and_known_students(tmp_path: Path) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    path = tmp_path / "saved.csv"
    path.write_text("student_id,room\n001,A\n")
    with pytest.raises(ValueError, match="need student_id,room,seat"):
        reuse_assignments(cfg, people(2), path)
    path.write_text("student_id,room,seat\nunknown,A,1A\n")
    with pytest.raises(ValueError, match="unknown/excluded"):
        reuse_assignments(cfg, people(2), path)
    path.write_text("student_id,room,seat\n00000001,A,1A\n")
    with pytest.raises(ValueError, match="cover the roster"):
        reuse_assignments(cfg, people(2), path)


def test_saved_csv_section_changes_require_resolution(tmp_path: Path) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    students = people(2)
    path = tmp_path / "saved.csv"
    assignments: list[Assignment] = assign(cfg, students)
    write_assignments(path, cfg, assignments)
    with pytest.raises(ValueError, match="section does not match"):
        reuse_assignments(cfg, [replace(students[0], section="2"), students[1]], path)
