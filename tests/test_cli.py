"""Exercise the public command, relative paths, and reuse/fresh options."""

import json
from pathlib import Path

import pytest

from generate import main
from seating import assign, read_config, read_roster, reuse_assignments

ROOT = Path(__file__).resolve().parents[1]


def test_cli_resolves_roster_relative_to_config_from_another_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(ROOT / "examples/mixed_rooms.toml"), "--output", "exam"]) == 0
    assert "Verified 12 students and 12 unique seats." in capsys.readouterr().out
    assert (tmp_path / "exam/EEE212_Door_Lists.pdf").is_file()


def test_cli_fresh_and_reuse_overrides(tmp_path: Path) -> None:
    path = ROOT / "examples/current_exam.toml"
    cfg, _ = read_config(path)
    students = read_roster(ROOT / "examples/current_roster.csv")
    assert main(["--config", str(path), "--output", str(tmp_path / "fresh"), "--fresh"]) == 0
    fresh = tmp_path / "fresh/EEE212_Assignments.csv"
    assert set(reuse_assignments(cfg, students, fresh)) == set(assign(cfg, students))
    manifest = json.loads((tmp_path / "fresh/manifest.json").read_text())
    assert manifest["reused_assignments"] is False
    assert (
        main(
            [
                "--config",
                str(path),
                "--output",
                str(tmp_path / "reuse"),
                "--reuse-assignments",
                str(fresh),
                "--roster",
                str(ROOT / "examples/current_roster.csv"),
            ]
        )
        == 0
    )
    assert fresh.read_bytes() == (tmp_path / "reuse/EEE212_Assignments.csv").read_bytes()


@pytest.mark.parametrize(
    "case", ["missing-roster", "conflicting-options", "missing-file", "bad-toml"]
)
def test_cli_input_errors_have_no_tracebacks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    path = tmp_path / "config.json"
    raw = json.loads((ROOT / "examples/mixed_rooms.json").read_text())
    del raw["roster_file"]
    args = ["--config", str(path), "--output", str(tmp_path / "exam")]
    if case == "conflicting-options":
        args += ["--roster", "unused.csv", "--fresh", "--reuse-assignments", "unused.csv"]
    elif case == "missing-file":
        raw["roster_file"] = "missing.csv"
    elif case == "bad-toml":
        path = tmp_path / "config.toml"
        args[1] = str(path)
    path.write_text("broken = [" if case == "bad-toml" else json.dumps(raw))
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert capsys.readouterr().err.startswith("Error:")
    assert not (tmp_path / "exam").exists()
