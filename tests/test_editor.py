"""The UI uses the same validators as the CLI and exports runnable configs."""

import json
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock

import pytest

import configure
from configure import EditorHandler, api_request, main
from seating import config_from_dict, parse_config
from tests.test_config import settings


def roster() -> str:
    return "student_id,given_name,surname,section\n001,Çağrı,Öztürk,1\n002,Another,Student,2\n003,Third,Student,2\n"


def editor_settings() -> dict[str, Any]:
    raw = settings()
    raw["course"] = "MATH 101"
    raw["room_defaults"]["student_count"] = 0
    raw["rooms"][1]["student_count"] = 0
    raw["roster_file"] = "students.csv"
    return raw


@pytest.mark.parametrize("policy", ["mixed", "separate_rooms", "keep_together"])
def test_auto_fit_exports_round_trip_through_real_config_parser(policy: str) -> None:
    raw = editor_settings()
    raw["section_policy"] = policy
    raw["reuse_assignments_file"] = "saved.csv"
    result = api_request("/api/fit", {"config": raw, "roster": roster()})
    expected = config_from_dict(result["config"])
    for config_format in ("toml", "json"):
        restored, _ = parse_config(result[config_format], config_format)
        assert restored == expected
    assert sum(r.student_count for r in expected.rooms) == 3
    assert result["roster_checked"] is True
    assert result["sections"] == {"1": 1, "2": 2}
    assert result["config"]["reuse_assignments_file"] == "saved.csv"
    assert result["saved_assignments_pending"] is True
    assert "room_defaults" in result["toml"]


def test_config_only_validation_and_import_preserve_all_settings() -> None:
    raw = editor_settings()
    raw["excluded_student_ids"] = ["004"]
    result = api_request("/api/check", {"config": raw})
    assert result["roster_checked"] is False
    assert result["sections"] == {}
    for config_format in ("toml", "json"):
        imported = api_request(
            "/api/import", {"content": result[config_format], "format": config_format}
        )
        assert config_from_dict(imported["config"]) == config_from_dict(result["config"])
        assert imported["config"]["excluded_student_ids"] == ["004"]
    raw["rooms"] = [
        {
            "name": 'Türkçe "Room"',
            "rows": 2,
            "columns": 2,
            "student_count": 0,
            "blocked_seats": ["1A"],
        }
    ]
    raw.pop("room_defaults")
    result = api_request("/api/check", {"config": raw})
    assert parse_config(result["toml"], "toml")[0].rooms[0].name == 'Türkçe "Room"'


@pytest.mark.parametrize(
    ("route", "body", "message"),
    [
        ("/api/import", {}, "Choose a TOML"),
        ("/api/import", {"content": "broken", "format": "toml"}, "Expected"),
        ("/api/unknown", {}, "Unknown"),
        ("/api/check", {"config": {}}, "Missing"),
        ("/api/fit", {"config": editor_settings()}, "Upload a roster"),
        ("/api/check", {"config": editor_settings(), "roster": 1}, "CSV text"),
        ("/api/check", {"config": editor_settings(), "roster": roster()}, "sum to"),
    ],
)
def test_invalid_actions_fail_with_explanations(
    route: str, body: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        api_request(route, body)


def request(
    server: ThreadingHTTPServer,
    method: str,
    route: str,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as connection:
        connection.request(method, route, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


def test_http_assets_and_api_work_without_exposing_repo_files(
    editor_server: ThreadingHTTPServer,
) -> None:
    for route in ("/", "/app.js", "/model.js", "/styles.css"):
        status, headers, body = request(editor_server, "GET", route)
        assert status == 200 and body
        assert headers["X-Content-Type-Options"] == "nosniff"
    for route in ("/seating.py", "/../examples/current_roster.csv", "/examples/current_roster.csv"):
        assert request(editor_server, "GET", route)[0] == 404
    status, _, body = request(
        editor_server, "POST", "/api/check", json.dumps({"config": editor_settings()})
    )
    assert status == 200
    assert json.loads(body)["config"]["course"] == "MATH 101"


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        ("{}", {"Origin": "https://foreign.example"}, 403),
        ("{}", {"Host": "foreign.example"}, 403),
        ("broken", {}, 400),
        ("[]", {}, 400),
        ("", {"Content-Length": "999999999"}, 400),
        ("", {"Content-Length": "invalid"}, 400),
    ],
)
def test_http_invalid_requests_and_foreign_origins_are_rejected(
    editor_server: ThreadingHTTPServer, body: str, headers: dict[str, str], status: int
) -> None:
    result = request(editor_server, "POST", "/api/check", body, headers)
    assert result[0] == status
    assert "error" in json.loads(result[2])


@pytest.mark.parametrize("no_browser", [False, True])
def test_launcher_and_clean_shutdown(monkeypatch: pytest.MonkeyPatch, no_browser: bool) -> None:
    server = MagicMock()
    server.__enter__.return_value = server
    server.server_port = 8765
    server.serve_forever.side_effect = KeyboardInterrupt
    factory = MagicMock(return_value=server)
    opener = MagicMock()
    monkeypatch.setattr(configure, "EditorServer", factory)
    monkeypatch.setattr(configure.webbrowser, "open", opener)
    assert main(["--no-browser"] if no_browser else []) == 0
    factory.assert_called_once_with(("127.0.0.1", 8765), EditorHandler)
    assert opener.call_count == (0 if no_browser else 1)


def test_launcher_port_errors_are_actionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["--port", "70000"])
    monkeypatch.setattr(configure, "EditorServer", MagicMock(side_effect=OSError("in use")))
    with pytest.raises(SystemExit):
        main(["--no-browser"])
    assert "Try another --port" in capsys.readouterr().err
