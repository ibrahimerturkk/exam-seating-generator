#!/usr/bin/env python3
"""Launch the local visual exam configuration editor."""

from __future__ import annotations

import argparse
import json
import mimetypes
import webbrowser
from collections import Counter
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from generate import geometry, register_fonts
from seating import (
    Config,
    active_students,
    assign,
    config_from_dict,
    parse_config,
    parse_roster,
    suggest_rooms,
)

ASSETS = Path(__file__).resolve().parent / "ui"
MAX_BODY = 2 * 1024 * 1024


def editor_config(config: Config, source: dict[str, Any]) -> dict[str, Any]:
    result = {
        "course": config.course,
        "exam_title": config.exam_title,
        "date": config.date.isoformat(),
        "time": config.time.strftime("%H:%M"),
        "seed": config.seed,
        "filename_prefix": config.filename_prefix,
        "sort_order": config.sort_order,
        "section_policy": config.section_policy,
        "excluded_student_ids": list(config.excluded_student_ids),
        "rooms": [
            {
                **asdict(room),
                "aisles_after": list(room.aisles_after),
                "blocked_seats": list(room.blocked_seats),
            }
            for room in config.rooms
        ],
    }
    for key in ("roster_file", "reuse_assignments_file"):
        if source.get(key):
            result[key] = source[key]
    return result


def compact_config(config: dict[str, Any]) -> dict[str, Any]:
    result = {k: v for k, v in config.items() if k != "rooms"}
    rooms = [dict(room) for room in config["rooms"]]
    defaults: dict[str, Any] = {}
    if len(rooms) > 1:
        for key in (
            "rows",
            "columns",
            "student_count",
            "aisles_after",
            "seating_policy",
            "paper_size",
        ):
            if all(room[key] == rooms[0][key] for room in rooms):
                defaults[key] = rooms[0][key]
                for room in rooms:
                    del room[key]
    for room in rooms:
        for key in ("blocked_seats", "section"):
            if not room.get(key):
                room.pop(key, None)
    if defaults:
        result["room_defaults"] = defaults
    result["rooms"] = rooms
    return result


def toml_text(config: dict[str, Any]) -> str:
    def value(item: Any) -> str:
        return json.dumps(item, ensure_ascii=False)

    lines = ["# Created with the exam room planner. Paths are relative to this file."]
    for key, item in config.items():
        if key not in ("rooms", "room_defaults"):
            lines.append(f"{key} = {value(item)}")
    if config.get("room_defaults"):
        lines += ["", "[room_defaults]"]
        lines += [f"{key} = {value(item)}" for key, item in config["room_defaults"].items()]
    for room in config["rooms"]:
        lines += ["", "[[rooms]]"]
        lines += [f"{key} = {value(item)}" for key, item in room.items()]
    return "\n".join(lines) + "\n"


def api_request(route: str, body: dict[str, Any]) -> dict[str, Any]:
    if route == "/api/import":
        if not isinstance(body.get("content"), str) or not isinstance(body.get("format"), str):
            raise ValueError("Choose a TOML or JSON configuration file.")
        config, source = parse_config(body["content"].lstrip("\ufeff"), body["format"])
        return {"config": editor_config(config, source)}
    if route not in ("/api/check", "/api/fit"):
        raise ValueError("Unknown editor action.")
    raw = body.get("config")
    config = config_from_dict(raw)
    source = cast(dict[str, Any], raw)
    roster = body.get("roster", "")
    if not isinstance(roster, str):
        raise ValueError("Roster contents must be CSV text.")
    students = parse_roster(roster) if roster else None
    if route == "/api/fit":
        if students is None:
            raise ValueError("Upload a roster before fitting students to rooms.")
        config = replace(config, rooms=suggest_rooms(config, students))
    assignments = assign(config, active_students(config, students)) if students is not None else []
    register_fonts()
    sizes = {
        room.name: geometry(room, [a for a in assignments if a.room == room.name])["paper"]
        for room in config.rooms
    }
    canonical = editor_config(config, source)
    compact = compact_config(canonical)
    return {
        "config": canonical,
        "toml": toml_text(compact),
        "json": json.dumps(compact, ensure_ascii=False, indent=2) + "\n",
        "roster_checked": students is not None,
        "saved_assignments_pending": bool(source.get("reuse_assignments_file")),
        "sections": dict(
            Counter(s.section or "No section" for s in active_students(config, students))
        )
        if students is not None
        else {},
        "rooms": [
            {
                "name": room.name,
                "paper": sizes[room.name],
                "available": len(room.available_seats),
                "sections": dict(
                    Counter(
                        a.student.section or "No section"
                        for a in assignments
                        if a.room == room.name
                    )
                ),
            }
            for room in config.rooms
        ],
    }


class EditorHandler(BaseHTTPRequestHandler):
    """Serve only editor assets; configuration paths never read local files."""

    def respond(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        route = urlsplit(self.path).path
        assets = {
            "/": "index.html",
            "/app.js": "app.js",
            "/model.js": "model.js",
            "/styles.css": "styles.css",
        }
        if route not in assets:
            self.respond(404, b"Not found", "text/plain; charset=utf-8")
            return
        path = ASSETS / assets[route]
        self.respond(
            200,
            path.read_bytes(),
            (mimetypes.guess_type(path.name)[0] or "text/plain") + "; charset=utf-8",
        )

    def do_POST(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        expected = f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"
        if host != expected or (origin and origin != f"http://{expected}"):
            self.respond(
                403, b'{"error":"Open the editor using its local URL."}', "application/json"
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Choose a file smaller than 2 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request must be an object.")
            result = api_request(urlsplit(self.path).path, body)
        except (ValueError, KeyError) as error:
            self.respond(400, json.dumps({"error": str(error)}).encode(), "application/json")
            return
        self.respond(
            200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # Do not log roster contents or other uploaded data.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    parser.add_argument(
        "--no-browser", action="store_true", help="Print the URL without opening a browser"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), EditorHandler) as server:
            url = f"http://127.0.0.1:{server.server_port}"
            print(f"Exam room planner: {url}\nPress Ctrl+C to stop.", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            server.serve_forever()
    except OSError as error:
        parser.exit(2, f"Error: {error}. Try another --port.\n")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
