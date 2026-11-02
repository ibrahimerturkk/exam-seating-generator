#!/usr/bin/env python3
"""Create exam seating PDFs from a roster and a TOML or JSON configuration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any, TypedDict
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A2, A3, A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from seating import (
    Assignment,
    Config,
    Room,
    active_students,
    assign,
    clean,
    column_label,
    parse_seat,
    read_config,
    read_roster,
    reuse_assignments,
    sort_key,
)

ROOT = Path(__file__).resolve().parent
INK = colors.HexColor("#19212D")
MUTED = colors.HexColor("#525A66")
LINE = colors.HexColor("#7F8790")
PALE = colors.HexColor("#F0F1F3")
MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def register_fonts() -> None:
    for name, file in (("Exam", "NotoSans-Regular.ttf"), ("Exam-Bold", "NotoSans-Bold.ttf")):
        path = ROOT / "fonts" / file
        if not path.is_file():
            raise ValueError(f"Missing bundled font: {path}")
        pdfmetrics.registerFont(TTFont(name, str(path)))


def draw_text(
    c: canvas.Canvas,
    x: float,
    y: float,
    value: object,
    size: float = 10,
    bold: bool = False,
    color: colors.Color = INK,
    align: str = "left",
) -> None:
    c.setFont("Exam-Bold" if bold else "Exam", size)
    c.setFillColor(color)
    {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[align](
        x, y, str(value)
    )


def fit_size(value: str, width: float, size: float, minimum: float, bold: bool = False) -> float:
    font = "Exam-Bold" if bold else "Exam"
    while pdfmetrics.stringWidth(value, font, size) > width and size > minimum:
        size = round(size - 0.2, 2)
    if pdfmetrics.stringWidth(value, font, size) > width:
        raise ValueError(f"Text is too wide for the page: {value!r}")
    return size


def wrap(
    value: str, width: float, size: float, bold: bool = False, max_lines: int = 2
) -> list[str]:
    font = "Exam-Bold" if bold else "Exam"
    lines = []
    current = ""
    for word in value.split():
        if pdfmetrics.stringWidth(word, font, size) > width:
            raise ValueError(f"Name does not fit the selected page width: {value!r}")
        proposed = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(proposed, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        raise ValueError(f"Name requires more than {max_lines} lines: {value!r}")
    return lines


def date_label(config: Config) -> str:
    value = config.date
    return f"{value.day} {MONTHS[value.month]} {value.year}"


class Geometry(TypedDict):
    paper: str
    width: float
    height: float
    seat_width: float
    seat_height: float
    top: float


def geometry(room: Room, assignments: list[Assignment]) -> Geometry:
    """Select a readable sheet size; never silently shrink a dense grid."""
    sizes = {"A4": landscape(A4), "A3": landscape(A3), "A2": landscape(A2)}
    options = sizes if room.paper_size == "auto" else {room.paper_size: sizes[room.paper_size]}
    last_problem = ""
    for paper, (w, h) in options.items():
        seat_w = (w - 45 - 24 - len(room.aisles_after) * 18) / room.columns
        seat_h = min(90, (h - 198 - (room.rows - 1) * 3) / room.rows)
        if seat_w < 110 or seat_h < 54:
            last_problem = "Seat boxes would be too small for names and signatures."
            continue
        try:
            for item in assignments:
                wrap(item.student.full_name, seat_w - 12, 9.4, True)
                header_width = (
                    pdfmetrics.stringWidth(item.seat, "Exam-Bold", 8.5)
                    + pdfmetrics.stringWidth(item.student.student_id, "Exam", 8)
                    + 20
                )
                if header_width > seat_w:
                    raise ValueError("Seat code and student ID do not fit in a seat box.")
        except ValueError as error:
            last_problem = str(error)
            continue
        return {
            "paper": paper,
            "width": w,
            "height": h,
            "seat_width": seat_w,
            "seat_height": seat_h,
            "top": h - 118,
        }
    raise ValueError(
        f"{room.name}: no readable {room.paper_size} seating layout. {last_problem} "
        "Use paper_size 'auto', a smaller grid, or split the room into smaller plans."
    )


def seating_page(
    c: canvas.Canvas, config: Config, room: Room, assignments: list[Assignment]
) -> Geometry:
    g = geometry(room, assignments)
    w, h, sw, sh = g["width"], g["height"], g["seat_width"], g["seat_height"]
    c.setPageSize((w, h))
    room_size = fit_size(room.name, w * 0.3, 26, 14, True)
    room_width = pdfmetrics.stringWidth(room.name, "Exam-Bold", room_size)
    title = f"{config.course} - {config.exam_title}"
    title_size = fit_size(title, w - 75 - room_width, 16, 11, True)
    draw_text(c, 24, h - 29, title, title_size, True)
    draw_text(c, 24, h - 47, "SEATING PLAN AND ATTENDANCE", 10, True, MUTED)
    draw_text(c, w - 24, h - 31, room.name, room_size, True, align="right")
    draw_text(c, 24, h - 66, f"Date: {date_label(config)}", 10)
    draw_text(c, 235, h - 66, f"Time: {config.time:%H:%M}", 10)
    draw_text(c, w - 24, h - 66, f"Student count: {len(assignments)}", 10, align="right")
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(24, h - 76, w - 24, h - 76)
    c.setFillColor(PALE)
    c.setStrokeColor(INK)
    board_w = min(250, w * 0.4)
    c.roundRect((w - board_w) / 2, h - 99, board_w, 23, 2, stroke=1, fill=1)
    draw_text(c, w / 2, h - 92, "BOARD / FRONT", 11, True, align="center")
    occupied = {a.seat: a for a in assignments}
    for col in range(room.columns):
        x = 45 + col * sw + sum(col >= boundary for boundary in room.aisles_after) * 18
        draw_text(c, x + sw / 2, h - 109, column_label(col), 10, True, align="center")
    for row in range(1, room.rows + 1):
        y = g["top"] - (row - 1) * (sh + 3) - sh
        draw_text(c, 29, y + sh / 2 - 4, row, 10, True, align="center")
        for col in range(room.columns):
            x = 45 + col * sw + sum(col >= boundary for boundary in room.aisles_after) * 18
            c.setFillColor(colors.white)
            c.setStrokeColor(LINE)
            c.setLineWidth(0.45)
            c.rect(x, y, sw, sh, stroke=1, fill=1)
            code = f"{row}{column_label(col)}"
            if code not in occupied:
                continue  # Unoccupied and blocked seats are both left blank.
            item = occupied[code]
            draw_text(c, x + 6, y + sh - 10, code, 8.5, True)
            draw_text(
                c, x + sw - 6, y + sh - 10, item.student.student_id, 8, color=MUTED, align="right"
            )
            for i, line in enumerate(wrap(item.student.full_name, sw - 12, 9.4, True)):
                draw_text(c, x + sw / 2, y + sh - 23 - i * 10.5, line, 9.4, True, align="center")
            draw_text(c, x + 6, y + 7, "Signature:", 6.8, color=MUTED)
            c.setLineWidth(0.35)
            c.line(x + 41, y + 6, x + sw - 7, y + 6)
        boundaries = (0, *room.aisles_after, room.columns)
        for left, right in pairwise(boundaries):
            x = 45 + left * sw + sum(left >= b for b in room.aisles_after) * 18
            c.setStrokeColor(INK)
            c.setLineWidth(0.75)
            c.rect(x, y, (right - left) * sw, sh, stroke=1, fill=0)
    last = column_label(room.columns - 1)
    note = f"Row 1 is nearest the board. Facing the board, A-{last} run left to right."
    draw_text(c, 45, 67, note, 8.2, color=MUTED)
    draw_text(c, 24, 47, "Students present: ________     Students absent: ________", 9)
    draw_text(
        c,
        24,
        28,
        "Proctor Name: ____________________________________     Signature: __________________________",
        9,
    )
    draw_text(c, w - 24, 13, f"{room.name} / {config.course}", 7.5, color=MUTED, align="right")
    c.showPage()
    return g


def door_list(c: canvas.Canvas, config: Config, room: Room, assignments: list[Assignment]) -> int:
    """Append one room, starting on a new page and repeating its header on overflow."""
    w, h = A4
    margin = 34
    edges = [margin, margin + 147, margin + 314, margin + 441, w - margin]
    people = sorted(assignments, key=lambda a: sort_key(a.student, config.sort_order))
    rows = []
    for item in people:
        values = [item.student.surname, item.student.given_name, item.student.student_id, item.seat]
        lines = [
            wrap(value, edges[j + 1] - edges[j] - 14, 10.5, j == 3, 3)
            for j, value in enumerate(values)
        ]
        rows.append((lines, max(19.7, max(map(len, lines)) * 12 + 7)))
    pages: list[list[tuple[list[list[str]], float]]] = []
    current: list[tuple[list[list[str]], float]] = []
    used = 0.0
    max_height = h - 155 - 26 - 90
    for row in rows:
        if current and used + row[1] > max_height:
            pages.append(current)
            current, used = [], 0.0
        current.append(row)
        used += row[1]
    if current or not pages:
        pages.append(current)
    c.setPageSize(A4)
    for page_index, page_rows in enumerate(pages):
        title = f"{config.course} - {config.exam_title}"
        draw_text(c, margin, h - 41, title, fit_size(title, w - 2 * margin, 18, 11, True), True)
        draw_text(
            c, margin, h - 95, room.name, fit_size(room.name, w - 2 * margin, 39, 18, True), True
        )
        draw_text(
            c, margin, h - 125, f"Date: {date_label(config)}     Time: {config.time:%H:%M}", 10.5
        )
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(margin, h - 138, w - margin, h - 138)
        top = h - 155
        c.setFillColor(INK)
        c.rect(margin, top - 26, w - 2 * margin, 26, stroke=0, fill=1)
        for j, label in enumerate(("Surname", "Given name", "Student ID", "Seat")):
            centered = j >= 2
            x = (edges[j] + edges[j + 1]) / 2 if centered else edges[j] + 7
            draw_text(
                c, x, top - 17, label, 10, True, colors.white, "center" if centered else "left"
            )
        y = top - 26
        for index, (lines, row_height) in enumerate(page_rows):
            y -= row_height
            if index % 2 == 0:
                c.setFillColor(PALE)
                c.rect(margin, y, w - 2 * margin, row_height, stroke=0, fill=1)
            for j, cell_lines in enumerate(lines):
                centered = j >= 2
                x = (edges[j] + edges[j + 1]) / 2 if centered else edges[j] + 7
                for k, line in enumerate(cell_lines):
                    draw_text(
                        c,
                        x,
                        y + row_height - 13 - 12 * k,
                        line,
                        10.5,
                        j == 3,
                        align="center" if centered else "left",
                    )
        c.setStrokeColor(LINE)
        c.line(margin, y, w - margin, y)
        example_row, example_col = min(3, room.rows), min(1, room.columns - 1)
        example = f"{example_row}{column_label(example_col)}"
        letters = (
            ", ".join(column_label(i) for i in range(room.columns))
            if room.columns <= 12
            else f"A to {column_label(room.columns - 1)}"
        )
        draw_text(c, margin, y - 22, "Use the Seat column to find your place.", 9.5)
        draw_text(
            c,
            margin,
            y - 39,
            f"Example: {example} = row {example_row}, seat {column_label(example_col)}. Row 1 is closest to the board.",
            9.5,
        )
        draw_text(
            c, margin, y - 56, f"Facing the board, seats run from left to right: {letters}.", 9.5
        )
        if len(pages) > 1:
            draw_text(
                c,
                w - margin,
                22,
                f"Page {page_index + 1} of {len(pages)}",
                8,
                color=MUTED,
                align="right",
            )
        c.showPage()
    return len(pages)


def door_lists(path: Path, config: Config, grouped: list[list[Assignment]]) -> dict[str, int]:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"{config.course} {config.exam_title} - Door Lists")
    c.setAuthor(config.course)
    pages = {}
    for index, (room, people) in enumerate(zip(config.rooms, grouped, strict=True)):
        bookmark = f"room-{index}"
        c.bookmarkPage(bookmark)
        c.addOutlineEntry(room.name, bookmark)
        pages[room.name] = door_list(c, config, room, people)
    c.save()
    return pages


def write_assignments(path: Path, config: Config, assignments: list[Assignment]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ("student_id", "given_name", "surname", "section", "room", "row", "position", "seat")
        )
        for item in sorted(assignments, key=lambda a: sort_key(a.student, config.sort_order)):
            row, col = parse_seat(item.seat)
            writer.writerow(
                (
                    item.student.student_id,
                    item.student.given_name,
                    item.student.surname,
                    item.student.section,
                    item.room,
                    row,
                    column_label(col),
                    item.seat,
                )
            )


def verify_pdf_text(
    path: Path,
    groups: list[list[Assignment]],
    rooms: tuple[Room, ...],
    page_counts: list[int] | None = None,
) -> None:
    """Check complete name/ID/seat entries exactly once within each room's pages."""
    reader = PdfReader(path)
    texts = [page.extract_text() or "" for page in reader.pages]
    counts = page_counts if page_counts is not None else [1] * len(groups)
    if len(counts) != len(groups) or len(rooms) != len(groups) or sum(counts) != len(texts):
        raise ValueError(f"Wrong page count in {path.name}.")
    offset = 0
    all_assignments = [item for group in groups for item in group]
    for room, group, count in zip(rooms, groups, counts, strict=True):
        room_pages = texts[offset : offset + count]
        offset += count
        if count < 1 or any(room.name not in text.splitlines() for text in room_pages):
            raise ValueError(f"Missing room heading for {room.name} in {path.name}.")
        text = clean("\n".join(room_pages))
        expected_ids = {a.student.student_id for a in group}
        for item in all_assignments:
            if page_counts is None:
                entry = f"{item.seat} {item.student.student_id} {item.student.full_name}"
            else:
                entry = f"{item.student.surname} {item.student.given_name} {item.student.student_id} {item.seat}"
            matches = re.findall(r"(?<!\S)" + re.escape(clean(entry)) + r"(?!\S)", text)
            expected = int(item.student.student_id in expected_ids)
            if len(matches) != expected:
                raise ValueError(
                    f"PDF text check failed for {item.student.student_id} in {path.name}. "
                    f"Expected {expected} complete name/ID/seat entries in {room.name}; "
                    f"found {len(matches)}."
                )


def build(
    config: Config,
    raw_config: dict[str, Any],
    roster_path: Path,
    output: Path,
    reuse_path: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build in a temporary directory and publish only after validation succeeds."""
    original = read_roster(roster_path)
    students = active_students(config, original)
    assignments = (
        reuse_assignments(config, students, reuse_path) if reuse_path else assign(config, students)
    )
    output = output.resolve()
    if output.exists():
        if not overwrite:
            raise ValueError(
                f"Output already exists: {output}. Choose a new folder or pass --overwrite."
            )
        marker = output / "manifest.json"
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if not isinstance(previous, dict) or previous.get("generator") != "exam-seating-generator":
            raise ValueError(
                "--overwrite only replaces folders previously created by this generator."
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    register_fonts()
    grouped = [[a for a in assignments if a.room == room.name] for room in config.rooms]
    for room, people in zip(config.rooms, grouped, strict=True):
        geometry(room, people)  # Preflight all room grids before creating PDFs.
    with tempfile.TemporaryDirectory(prefix=".exam-seating-", dir=output.parent) as temporary:
        stage = Path(temporary)
        seat_path = stage / f"{config.filename_prefix}_Seating_Plans.pdf"
        c = canvas.Canvas(str(seat_path))
        c.setTitle(f"{config.course} {config.exam_title} - Seating Plans")
        c.setAuthor(config.course)
        layouts = {}
        for room, people in zip(config.rooms, grouped, strict=True):
            layouts[room.name] = seating_page(c, config, room, people)
        c.save()
        verify_pdf_text(seat_path, grouped, config.rooms)
        door_path = stage / f"{config.filename_prefix}_Door_Lists.pdf"
        door_pages = door_lists(door_path, config, grouped)
        verify_pdf_text(door_path, grouped, config.rooms, list(door_pages.values()))
        generated = [seat_path, door_path]
        csv_path = stage / f"{config.filename_prefix}_Assignments.csv"
        write_assignments(csv_path, config, assignments)
        # Re-import the saved CSV to catch export omissions and identity/seat mismatches.
        exported = reuse_assignments(config, students, csv_path)
        expected = {(a.student.student_id, a.room, a.seat) for a in assignments}
        if expected != {(a.student.student_id, a.room, a.seat) for a in exported}:
            raise ValueError("Saved assignment CSV did not round-trip correctly.")
        generated.append(csv_path)
        report = {
            "result": "PASS",
            "input_students": len(original),
            "excluded_students": len(original) - len(students),
            "assigned_students": len(assignments),
            "unique_room_seats": len({(a.room, a.seat) for a in assignments}),
            "room_counts": {
                room.name: len(people) for room, people in zip(config.rooms, grouped, strict=True)
            },
            "missing_students": [],
            "extra_students": [],
            "duplicate_students": [],
            "duplicate_seats": [],
            "all_student_names_and_ids_found_in_pdfs": True,
            "assignment_csv_round_trip": True,
            "seating_paper_sizes": {r: g["paper"] for r, g in layouts.items()},
            "door_list_file": door_path.name,
            "door_list_total_pages": sum(door_pages.values()),
            "door_list_pages": door_pages,
        }
        report_path = stage / "Validation_Report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        generated.append(report_path)
        manifest = {
            "generator": "exam-seating-generator",
            "version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "seed": config.seed,
            "roster_sha256": hashlib.sha256(roster_path.read_bytes()).hexdigest(),
            "reused_assignments": reuse_path is not None,
            "config": raw_config,
            "files": [p.name for p in generated],
            "assignments": [
                {**asdict(a.student), "room": a.room, "seat": a.seat} for a in assignments
            ],
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        generated.append(manifest_path)
        bundle = stage / f"{config.filename_prefix}_Exam_Package.zip"
        with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
            for path in generated:
                archive.write(path, path.name)
        with ZipFile(bundle) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity check failed.")
            if sorted(archive.namelist()) != sorted(p.name for p in generated) or any(
                archive.read(p.name) != p.read_bytes() for p in generated
            ):
                raise ValueError("ZIP contents do not match the generated files.")
        backup = None
        if output.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = output.with_name(f"{output.name}.backup-{stamp}")
            output.rename(backup)
        try:
            # Rename the complete validated build, not a collection of partial files.
            stage.rename(output)
        except OSError:
            if backup is not None:
                backup.rename(output)
            raise
    return {**report, "output": str(output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, type=Path, help="TOML or JSON exam and room settings"
    )
    parser.add_argument("--roster", type=Path, help="Override roster_file from the configuration")
    parser.add_argument(
        "--reuse-assignments", type=Path, help="Reuse a previously saved assignment CSV"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore reuse_assignments_file and generate from the seed",
    )
    parser.add_argument("--output", required=True, type=Path, help="Destination folder")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace a previous generated folder, retaining a backup",
    )
    args = parser.parse_args(argv)
    try:
        config, raw = read_config(args.config)
        base = args.config.resolve().parent
        roster = args.roster or (base / raw["roster_file"] if raw.get("roster_file") else None)
        if roster is None:
            raise ValueError("Provide --roster or set roster_file in the configuration.")
        if args.fresh and args.reuse_assignments:
            raise ValueError("Choose --fresh or --reuse-assignments, not both.")
        saved = args.reuse_assignments
        if saved is None and not args.fresh and raw.get("reuse_assignments_file"):
            saved = base / raw["reuse_assignments_file"]
        result = build(config, raw, roster, args.output, saved, args.overwrite)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Error: {error}\n")
    print(
        f"Verified {result['assigned_students']} students and {result['unique_room_seats']} unique seats."
    )
    for room, count in result["room_counts"].items():
        print(f"  {room}: {count}")
    print(f"Files: {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
