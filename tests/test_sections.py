"""Section constraints must hold for new allocations and saved reprints."""

import itertools
import random
from dataclasses import replace
from pathlib import Path

import pytest

from generate import build, write_assignments
from seating import (
    Room,
    Student,
    assign,
    config_from_dict,
    reuse_assignments,
    section_plan,
    suggest_rooms,
    validate_assignments,
)
from tests.test_config import settings
from tests.test_generator import config, roster_csv


def section_students(*sizes: int) -> list[Student]:
    return [
        Student(f"{section}-{number:03}", "Student", f"Name{number}", str(section))
        for section, size in enumerate(sizes, 1)
        for number in range(size)
    ]


@pytest.mark.parametrize("policy", ["mixed", "separate_rooms", "keep_together"])
def test_section_policy_parses_and_invalid_policies_fail(policy: str) -> None:
    raw = settings()
    raw["section_policy"] = policy
    assert config_from_dict(raw).section_policy == policy
    raw["section_policy"] = True
    with pytest.raises(ValueError, match="section_policy"):
        config_from_dict(raw)
    raw["section_policy"] = "mixed"
    raw["rooms"][0]["section"] = "1"
    with pytest.raises(ValueError, match="separate section policy"):
        config_from_dict(raw)
    raw["rooms"][0]["section"] = 1
    with pytest.raises(ValueError, match="must be a string"):
        config_from_dict(raw)


def test_separate_rooms_may_split_a_large_section_and_are_reproducible() -> None:
    cfg = replace(
        config([Room("A", 2, 3, 4), Room("B", 2, 3, 4), Room("C", 2, 3, 3)]),
        section_policy="separate_rooms",
    )
    students = section_students(8, 3)
    assignments = assign(cfg, students)
    assert assignments == assign(cfg, list(reversed(students)))
    assert len({a.student.student_id for a in assignments}) == 11
    for room in cfg.rooms:
        assert len({a.student.section for a in assignments if a.room == room.name}) == 1
    assert len({a.room for a in assignments if a.student.section == "1"}) == 2
    assert set(assign(replace(cfg, seed="different"), students)) != set(assignments)


def test_keep_together_puts_each_section_in_exactly_one_room() -> None:
    cfg = replace(
        config([Room("Small", 2, 3, 3), Room("Large", 3, 3, 8)]), section_policy="keep_together"
    )
    assignments = assign(cfg, section_students(8, 3))
    assert {a.room for a in assignments if a.student.section == "1"} == {"Large"}
    assert {a.room for a in assignments if a.student.section == "2"} == {"Small"}


@pytest.mark.parametrize("policy", ["separate_rooms", "keep_together"])
def test_unknown_and_missing_sections_have_clear_errors(policy: str) -> None:
    cfg = replace(config([Room("A", 2, 2, 2, section="unknown")]), section_policy=policy)
    with pytest.raises(ValueError, match="not in the active roster"):
        assign(cfg, section_students(2))
    with pytest.raises(ValueError, match="Every student needs a section"):
        assign(cfg, [Student("001", "A", "B")])


def test_impossible_fixed_counts_do_not_silently_mix_or_drop_students() -> None:
    cfg = replace(config([Room("A", 2, 2, 4), Room("B", 2, 2, 4)]), section_policy="separate_rooms")
    with pytest.raises(ValueError, match="Adjust room counts"):
        assign(cfg, section_students(5, 3))
    cfg = replace(config([Room("A", 2, 3, 4), Room("B", 2, 3, 4)]), section_policy="keep_together")
    with pytest.raises(ValueError, match="Cannot keep sections"):
        assign(cfg, section_students(8))


def test_fixed_planner_backtracks_when_first_section_choice_cannot_fit() -> None:
    cfg = replace(
        config([Room("A", 1, 5, 5), Room("B", 1, 4, 4), Room("C", 1, 3, 3)]),
        section_policy="separate_rooms",
    )
    assert section_plan(cfg, section_students(7, 5)) == [("2", 5), ("1", 4), ("1", 3)]


@pytest.mark.parametrize("policy", ["mixed", "separate_rooms", "keep_together"])
def test_auto_fit_respects_capacities_exclusions_and_policy(policy: str) -> None:
    students = section_students(7, 5)
    cfg = replace(
        config(
            [Room("A", 2, 4, 0, blocked_seats=("1A",)), Room("B", 2, 3, 0), Room("Empty", 1, 1, 0)]
        ),
        section_policy=policy,
        excluded_student_ids=(students[-1].student_id,),
    )
    rooms = suggest_rooms(cfg, students)
    assert not any(room.section for room in rooms)
    assert sum(room.student_count for room in rooms) == 11
    assert all(room.student_count <= len(room.available_seats) for room in rooms)
    active = students[:-1]
    validate_assignments(
        replace(cfg, rooms=rooms), active, assign(replace(cfg, rooms=rooms), active)
    )
    assert all(room.student_count == 0 for room in cfg.rooms)


def test_auto_fit_keeps_automatic_sections_editable_after_resizing() -> None:
    cfg = replace(
        config([Room("A", 2, 4, 0), Room("B", 2, 4, 0), Room("C", 2, 4, 0)]),
        section_policy="keep_together",
    )
    students = section_students(8, 4)
    rooms = suggest_rooms(cfg, students)
    resized = (replace(rooms[0], columns=3, student_count=6), *rooms[1:])
    fitted = suggest_rooms(replace(cfg, rooms=resized), students)
    assert sum(r.student_count for r in fitted) == 12
    assert all(not r.section for r in fitted)
    validate_assignments(
        replace(cfg, rooms=fitted), students, assign(replace(cfg, rooms=fitted), students)
    )


def test_auto_fit_obeys_explicit_section_choices_and_insufficient_capacity() -> None:
    cfg = replace(
        config([Room("A", 2, 3, 0, section="2"), Room("B", 2, 3, 0, section="1")]),
        section_policy="keep_together",
    )
    fitted = suggest_rooms(cfg, section_students(5, 4))
    assert [(r.section, r.student_count) for r in fitted] == [("2", 4), ("1", 5)]
    for policy in ("mixed", "separate_rooms", "keep_together"):
        with pytest.raises(ValueError, match="Not enough|Cannot keep sections"):
            suggest_rooms(
                replace(cfg, rooms=(Room("A", 1, 1, 0),), section_policy=policy),
                section_students(5, 4),
            )


def test_saved_assignments_are_checked_against_section_rules(tmp_path: Path) -> None:
    students = section_students(4, 4)
    cfg = config([Room("A", 2, 2, 4), Room("B", 2, 2, 4)])
    mixed = assign(cfg, students)
    assert any(len({a.student.section for a in mixed if a.room == r.name}) > 1 for r in cfg.rooms)
    path = tmp_path / "saved.csv"
    write_assignments(path, cfg, mixed)
    with pytest.raises(ValueError, match="different sections"):
        reuse_assignments(replace(cfg, section_policy="separate_rooms"), students, path)
    separate = replace(cfg, section_policy="separate_rooms")
    write_assignments(path, separate, assign(separate, students))
    assert len(reuse_assignments(separate, students, path)) == 8
    locked = replace(separate, rooms=(replace(cfg.rooms[0], section="wrong"), cfg.rooms[1]))
    with pytest.raises(ValueError, match="do not match the configured section"):
        reuse_assignments(locked, students, path)
    cfg = replace(cfg, section_policy="separate_rooms")
    split = assign(cfg, section_students(8))
    with pytest.raises(ValueError, match="split across rooms"):
        validate_assignments(
            replace(cfg, section_policy="keep_together"), section_students(8), split
        )
    missing = [replace(a, student=replace(a.student, section="")) for a in split]
    with pytest.raises(ValueError, match="every student needs a section"):
        validate_assignments(cfg, section_students(8), missing)


def test_zero_student_rooms_and_empty_active_roster() -> None:
    cfg = replace(config([Room("Empty", 1, 1, 0)]), section_policy="keep_together")
    assert assign(cfg, []) == []
    assert section_plan(cfg, []) == [("", 0)]


@pytest.mark.parametrize("policy", ["separate_rooms", "keep_together"])
def test_small_fixed_plans_match_exhaustive_feasibility(policy: str) -> None:
    rng = random.Random(4)
    for _ in range(50):
        counts = [rng.randint(1, 5) for _ in range(4)]
        cut = rng.randint(1, sum(counts) - 1)
        sizes = [cut, sum(counts) - cut]
        feasible = any(
            all(
                sum(count for count, section in zip(counts, choices, strict=True) if section == i)
                == sizes[i]
                for i in range(2)
            )
            and (policy != "keep_together" or all(choices.count(i) == 1 for i in range(2)))
            for choices in itertools.product(range(2), repeat=4)
        )
        cfg = replace(
            config([Room(f"R{i}", 1, 5, count) for i, count in enumerate(counts)]),
            section_policy=policy,
        )
        if feasible:
            assert len(assign(cfg, section_students(*sizes))) == sum(sizes)
        else:
            with pytest.raises(ValueError, match="Cannot keep sections"):
                assign(cfg, section_students(*sizes))


def test_other_courses_generate_section_aware_packages(tmp_path: Path) -> None:
    cfg = replace(
        config([Room("M1", 2, 3, 3), Room("M2", 2, 3, 4)]),
        course="MATH 101",
        filename_prefix="MATH101",
        section_policy="keep_together",
    )
    roster = tmp_path / "students.csv"
    roster_csv(roster, section_students(4, 3))
    report = build(cfg, {}, roster, tmp_path / "exam")
    assert report["section_policy"] == "keep_together"
    assert report["room_sections"] == {"M1": ["2"], "M2": ["1"]}
    assert (tmp_path / "exam/MATH101_Door_Lists.pdf").exists()
