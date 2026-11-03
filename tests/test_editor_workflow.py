"""Generate real packages over HTTP, import room libraries, and detect list.csv."""

import io
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from pypdf import PdfReader

import editor_workflow as workflow
from configure import EditorServer
from editor_workflow import (
    DownloadStore,
    GeneratedExam,
    generate_exam,
    nearby_roster,
    parse_room_library,
    room_library,
    roster_summary,
    workflow_action,
)
from tests.test_editor import editor_settings, request, roster

ROOT = Path(__file__).resolve().parents[1]


def ready_config() -> dict[str, Any]:
    raw = editor_settings()
    raw["rooms"][0]["student_count"] = 1
    raw["rooms"][1]["student_count"] = 2
    raw["section_policy"] = "keep_together"
    return raw


def test_libraries_import_templates_and_full_configs_without_exam_data() -> None:
    library = parse_room_library((ROOT / "examples/rooms.toml").read_text(), "toml")
    assert [r["name"] for r in library["rooms"]] == ["EE03", "EE04", "EE05"]
    assert all("student_count" not in r and "section" not in r for r in library["rooms"])
    assert library["rooms"][0]["aisles_after"] == [2, 4]
    restored = parse_room_library(json.dumps(library), "json")
    assert restored == library
    exam = parse_room_library((ROOT / "examples/current_exam.toml").read_text(), "toml")
    assert exam == library
    raw = editor_settings()
    raw["rooms"][0]["blocked_seats"] = ["1A"]
    raw["rooms"][0]["section"] = "1"
    raw["section_policy"] = "separate_rooms"
    assert room_library(raw)["rooms"][0]["blocked_seats"] == ["1A"]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"version": 2, "rooms": []},
        {"version": True, "rooms": []},
        {"unexpected": 1, "rooms": []},
        {"rooms": [1]},
        {"rooms": [{"name": "A", "rows": 1, "columns": 2, "typo": 1}]},
        {"rooms": [{"name": "A", "rows": 0, "columns": 2}]},
        {"room_defaults": [], "rooms": [{}]},
    ],
)
def test_invalid_room_libraries_are_rejected(raw: Any) -> None:
    with pytest.raises(ValueError):
        room_library(raw)


def test_nearby_roster_detects_exact_legacy_format_without_exposing_it_on_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "list.csv"
    monkeypatch.setattr(workflow, "NEARBY_ROSTER", path)
    assert nearby_roster() == {"found": False, "filename": "list.csv"}
    with pytest.raises(ValueError, match="No list.csv"):
        nearby_roster(load=True)
    content = "\ufeff\r\nSection: 1\r\n1\tÖztürk\tÇağrı İhsan\t00000001\r\n2\tErginbaş    Alp Emre\t00000002\r\n\r\nSection: 2\r\n1\tGül\tİhsan Atalay\t00000003 \t\r\n"
    path.write_bytes(content.encode("utf-8"))
    assert "content" not in nearby_roster()
    result = workflow_action("/api/nearby-roster", {"path": "/unrelated/private/file"})
    assert result["students"] == 3
    assert result["sections"] == {"1": 2, "2": 1}
    assert result["format"] == "section_list"
    assert "Çağrı İhsan" in result["content"]
    path.write_bytes(b"x" * (workflow.MAX_UPLOAD + 1))
    with pytest.raises(ValueError, match="smaller than 1 MB"):
        nearby_roster(load=True)


def test_roster_summary_handles_tables_and_does_not_return_student_names() -> None:
    result = roster_summary(roster())
    assert result["format"] == "table" and result["students"] == 3
    assert "Çağrı" not in json.dumps(result, ensure_ascii=False)
    with pytest.raises(ValueError, match="Duplicate student ID"):
        roster_summary(roster() + "001,Other,Person,1\n")


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("/api/roster", {}),
        ("/api/roster", {"content": 1}),
        ("/api/roster", {"content": "a", "filename": 1}),
        ("/api/roster", {"content": "x" * (workflow.MAX_UPLOAD + 1)}),
        ("/api/library/import", {}),
        ("/api/library/import", {"content": "{", "format": "json"}),
        ("/api/library/save", {}),
        ("/unknown", {}),
    ],
)
def test_bad_workflow_requests_are_actionable(route: str, body: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        workflow_action(route, body)


def test_web_generation_produces_real_verified_files_and_preserves_saved_seats() -> None:
    raw = ready_config()
    exam = generate_exam({"config": raw, "roster": roster()})
    assert exam.report["result"] == "PASS"
    assert exam.report["assigned_students"] == 3
    assert "output" not in exam.report
    assert len(PdfReader(io.BytesIO(exam.files["MATH101_Door_Lists.pdf"])).pages) == 2
    with ZipFile(io.BytesIO(exam.files["MATH101_Exam_Package.zip"])) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(exam.files) - {"MATH101_Exam_Package.zip"}
    raw["reuse_assignments_file"] = "original.csv"
    saved = exam.files["MATH101_Assignments.csv"].decode("utf-8-sig")
    replay = generate_exam({"config": raw, "roster": roster(), "assignments": saved})
    assert replay.files["MATH101_Assignments.csv"] == exam.files["MATH101_Assignments.csv"]
    assert json.loads(replay.files["manifest.json"])["reused_assignments"] is True
    fresh = generate_exam({"config": raw, "roster": roster(), "fresh": True})
    manifest = json.loads(fresh.files["manifest.json"])
    assert manifest["reused_assignments"] is False
    assert "reuse_assignments_file" not in manifest["config"]
    raw.pop("reuse_assignments_file")
    uploaded = generate_exam({"config": raw, "roster": roster(), "assignments": saved})
    assert json.loads(uploaded.files["manifest.json"])["reused_assignments"] is True


@pytest.mark.parametrize(
    "case",
    ["missing-roster", "bad-roster", "missing-saved", "bad-types", "conflicting", "bad-saved"],
)
def test_generation_refuses_incomplete_or_invalid_inputs(case: str) -> None:
    raw = ready_config()
    body: dict[str, Any] = {"config": raw, "roster": roster()}
    if case == "missing-roster":
        body.pop("roster")
    elif case == "bad-roster":
        body["roster"] = "bad"
    elif case == "missing-saved":
        raw["reuse_assignments_file"] = "/any/path.csv"
    elif case == "bad-types":
        body["fresh"] = "true"
    elif case == "conflicting":
        body.update(fresh=True, assignments="saved")
    else:
        body["assignments"] = "student_id,room,seat\nunknown,A,1A\n"
    with pytest.raises(ValueError):
        generate_exam(body)


def test_downloads_expire_are_bounded_and_do_not_allow_arbitrary_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1.0]
    monkeypatch.setattr(workflow.time, "monotonic", lambda: now[0])
    store = DownloadStore()
    exam = GeneratedExam({"result": "PASS"}, {"exam.pdf": b"%PDF-test"})
    first = store.add(exam)["files"][0]["url"].split("/")[2]
    assert store.get(first, "exam.pdf") == (b"%PDF-test", "application/pdf")
    assert store.get(first, "../secret") is None
    assert store.get("unknown", "exam.pdf") is None
    for _ in range(5):
        store.add(exam)
    assert len(store.runs) == 5 and store.get(first, "exam.pdf") is None
    last = next(iter(store.runs))
    now[0] += 3600
    assert store.get(last, "exam.pdf") is None and not store.runs


def test_generation_downloads_and_workflow_routes_over_http(
    editor_server: EditorServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "list.csv"
    path.write_text(roster())
    monkeypatch.setattr(workflow, "NEARBY_ROSTER", path)
    assert request(editor_server, "GET", "/api/library")[0] == 200
    assert "content" not in json.loads(request(editor_server, "GET", "/api/nearby-roster")[2])
    status, _, payload = request(editor_server, "POST", "/api/nearby-roster", "{}")
    assert status == 200 and json.loads(payload)["students"] == 3
    assert request(editor_server, "GET", "/api/missing")[0] == 404
    assert (
        request(editor_server, "GET", "/api/library", headers={"Host": "foreign.example"})[0] == 403
    )
    status, _, payload = request(
        editor_server,
        "POST",
        "/api/generate",
        json.dumps({"config": ready_config(), "roster": roster()}),
    )
    assert status == 200
    generated = json.loads(payload)
    assert generated["report"]["result"] == "PASS"
    for file in generated["files"]:
        status, headers, content = request(editor_server, "GET", file["url"])
        assert status == 200 and content
        assert file["name"] in headers["Content-Disposition"]
    assert request(editor_server, "GET", "/downloads/unknown/file.pdf")[0] == 404
    assert request(editor_server, "GET", "/downloads/invalid")[0] == 404
    before = len(editor_server.downloads.runs)
    assert (
        request(editor_server, "POST", "/api/generate", json.dumps({"config": ready_config()}))[0]
        == 400
    )
    assert len(editor_server.downloads.runs) == before


def test_library_load_error_is_an_api_error(
    editor_server: EditorServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workflow, "DEFAULT_LIBRARY", tmp_path / "missing.toml")
    status, _, body = request(editor_server, "GET", "/api/library")
    assert status == 400 and "error" in json.loads(body)
