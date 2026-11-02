"""End-to-end PDF content, pagination, bundles, and transactional publication."""

import json
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import Destination

import generate
from generate import build, verify_pdf_text
from seating import Assignment, Room, assign, read_config, read_roster, reuse_assignments, sort_key
from tests.test_generator import config, people, roster_csv

ROOT = Path(__file__).resolve().parents[1]


def test_combined_doors_keep_room_order_pagination_sorting_and_empty_rooms(tmp_path: Path) -> None:
    cfg = config(
        [
            Room("Large", 10, 8, 65, (4,)),
            Room("Boş Oda", 2, 2, 0),
            Room("Small", 2, 2, 3),
        ]
    )
    roster = tmp_path / "students.csv"
    students = people(68)
    roster_csv(roster, students)
    output = tmp_path / "output"
    report = build(cfg, {}, roster, output)
    assert report["door_list_pages"] == {"Large": 3, "Boş Oda": 1, "Small": 1}
    assert report["door_list_total_pages"] == 5
    assert report["door_list_file"] == "EEE212_Door_Lists.pdf"
    assert sorted(p.name for p in output.glob("*Door*.pdf")) == ["EEE212_Door_Lists.pdf"]
    reader = PdfReader(output / report["door_list_file"])
    assert len(reader.pages) == 5
    outline = reader.outline
    assert all(isinstance(entry, Destination) for entry in outline)
    destinations = [entry for entry in outline if isinstance(entry, Destination)]
    assert [entry.title for entry in destinations] == [r.name for r in cfg.rooms]
    assert [reader.get_destination_page_number(entry) for entry in destinations] == [0, 3, 4]
    assignments = assign(cfg, students)
    offset = 0
    for room in cfg.rooms:
        count = report["door_list_pages"][room.name]
        pages = reader.pages[offset : offset + count]
        offset += count
        text = " ".join(" ".join(p.extract_text().split()) for p in pages)
        expected = sorted(
            (a for a in assignments if a.room == room.name), key=lambda a: sort_key(a.student)
        )
        positions = [text.index(a.student.student_id) for a in expected]
        assert positions == sorted(positions)
        for index, page in enumerate(pages, 1):
            assert tuple(round(float(v), 1) for v in page.mediabox[2:]) == (595.3, 841.9)
            content = page.extract_text()
            assert room.name in content.splitlines()
            assert "Surname" in content and "Student ID" in content
            if count > 1:
                assert f"Page {index} of {count}" in content
    manifest = json.loads((output / "manifest.json").read_text())
    assert report["door_list_file"] in manifest["files"]
    assert len([name for name in manifest["files"] if "Door" in name]) == 1
    with ZipFile(output / "EEE212_Exam_Package.zip") as archive:
        assert set(archive.namelist()) == set(manifest["files"]) | {"manifest.json"}
        for name in archive.namelist():
            assert archive.read(name) == (output / name).read_bytes()


def test_approved_exam_preserves_all_81_saved_assignments(tmp_path: Path) -> None:
    cfg, raw = read_config(ROOT / "examples/current_exam.toml")
    roster = ROOT / "examples/current_roster.csv"
    saved = ROOT / "examples/current_assignments.csv"
    report = build(cfg, raw, roster, tmp_path / "exam", saved)
    assert report["assigned_students"] == report["unique_room_seats"] == 81
    assert report["room_counts"] == {"EE03": 27, "EE04": 27, "EE05": 27}
    students = read_roster(roster)
    exported = reuse_assignments(cfg, students, tmp_path / "exam/EEE212_Assignments.csv")
    assert set(exported) == set(reuse_assignments(cfg, students, saved))


def test_pdf_verifier_rejects_wrong_seat_wrong_room_missing_or_duplicate_entries(
    tmp_path: Path,
) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    roster = tmp_path / "students.csv"
    students = people(2)
    roster_csv(roster, students)
    build(cfg, {}, roster, tmp_path / "exam")
    assignments = assign(cfg, students)
    seat_pdf = tmp_path / "exam/EEE212_Seating_Plans.pdf"
    door_pdf = tmp_path / "exam/EEE212_Door_Lists.pdf"
    wrong = [replace(assignments[0], seat="99Z"), assignments[1]]
    for path, counts in ((seat_pdf, None), (door_pdf, [1])):
        with pytest.raises(ValueError, match="PDF text check failed"):
            verify_pdf_text(path, [wrong], cfg.rooms, counts)
        writer = PdfWriter()
        original_page = PdfReader(path).pages[0]
        writer.add_page(original_page)
        writer.pages[0].merge_page(original_page)
        duplicate = tmp_path / f"duplicate-{path.name}"
        writer.write(duplicate)
        with pytest.raises(ValueError, match="PDF text check failed"):
            verify_pdf_text(duplicate, [assignments], cfg.rooms, counts)
        with pytest.raises(ValueError, match="Missing room heading"):
            verify_pdf_text(path, [assignments], (replace(cfg.rooms[0], name="Wrong"),), counts)
        with pytest.raises(ValueError, match="page count"):
            verify_pdf_text(path, [assignments], cfg.rooms, [2])
        missing = [Assignment(replace(students[0], student_id="missing"), "A", "1A")]
        with pytest.raises(ValueError, match="PDF text check failed"):
            verify_pdf_text(path, [missing], cfg.rooms, counts)


def test_failed_pdf_generation_leaves_previous_output_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    roster = tmp_path / "students.csv"
    roster_csv(roster, people(2))
    output = tmp_path / "exam"
    build(cfg, {}, roster, output)
    original = {p.name: p.read_bytes() for p in output.iterdir()}

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("Simulated PDF failure")

    monkeypatch.setattr(generate, "verify_pdf_text", fail)
    with pytest.raises(ValueError, match="Simulated PDF failure"):
        build(cfg, {}, roster, output, overwrite=True)
    assert original == {p.name: p.read_bytes() for p in output.iterdir()}
    assert not list(tmp_path.glob(".exam-seating-*"))
    assert not list(tmp_path.glob("exam.backup-*"))


def test_failed_publication_restores_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    roster = tmp_path / "students.csv"
    roster_csv(roster, people(2))
    output = tmp_path / "exam"
    build(cfg, {}, roster, output)
    original = (output / "manifest.json").read_bytes()
    rename = Path.rename

    def fail_stage(self: Path, target: str | Path) -> Path:
        if self.name.startswith(".exam-seating-"):
            raise OSError("Simulated rename failure")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_stage)
    with pytest.raises(OSError, match="Simulated rename failure"):
        build(cfg, {}, roster, output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == original
    assert not list(tmp_path.glob(".exam-seating-*"))
    assert not list(tmp_path.glob("exam.backup-*"))


@pytest.mark.parametrize("marker", [None, "{}", "[]", "invalid JSON"])
def test_overwrite_refuses_unrecognized_folders(tmp_path: Path, marker: str | None) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    roster = tmp_path / "students.csv"
    roster_csv(roster, people(2))
    output = tmp_path / "exam"
    output.mkdir()
    if marker is not None:
        (output / "manifest.json").write_text(marker)
    with pytest.raises(ValueError, match="only replaces"):
        build(cfg, {}, roster, output, overwrite=True)
    assert output.is_dir()


@pytest.mark.parametrize("failure", ["integrity", "contents"])
def test_invalid_bundle_is_not_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    cfg = config([Room("A", 2, 2, 2)])
    roster = tmp_path / "students.csv"
    roster_csv(roster, people(2))
    if failure == "integrity":
        monkeypatch.setattr(ZipFile, "testzip", lambda self: "corrupt.pdf")
    else:
        monkeypatch.setattr(ZipFile, "namelist", lambda self: [])
    with pytest.raises(ValueError, match="ZIP"):
        build(cfg, {}, roster, tmp_path / "exam")
    assert not (tmp_path / "exam").exists()
    assert not list(tmp_path.glob(".exam-seating-*"))
