"""CLI tests via typer's CliRunner."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from swpilot.cli import app

EXAMPLES = Path(__file__).parent.parent / "examples"
runner = CliRunner()


@pytest.fixture
def example(tmp_path: Path) -> Path:
    dst = tmp_path / "plate.json"
    shutil.copy(EXAMPLES / "plate_with_holes.json", dst)
    return dst


class TestValidate:
    def test_valid_file(self, example: Path) -> None:
        result = runner.invoke(app, ["validate", str(example)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 2

    def test_schema_error_reports_location(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "commands": [{"op": "draw_circle", "diameter": -5}],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 2
        assert "commands.0" in result.output

    def test_macro_error_caught_at_validate_time(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "commands": [{"op": "add_corner_holes", "diameter": 8, "margin": 10}],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 2
        assert "create_plate" in result.output


class TestExpand:
    def test_expand_outputs_primitives(self, example: Path) -> None:
        result = runner.invoke(app, ["expand", str(example)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert [d["op"] for d in data[:4]] == [
            "new_part",
            "create_sketch",
            "draw_rectangle",
            "extrude",
        ]
        assert data[0]["expanded_from"] == "create_plate"


class TestRun:
    def test_run_mock_writes_report(self, example: Path) -> None:
        result = runner.invoke(app, ["run", str(example)])
        assert result.exit_code == 0, result.output
        assert "success" in result.output
        report_path = example.parent / "plate.json.report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["success"] is True
        assert report["backend"] == "mock"

    def test_run_custom_report_path(self, example: Path, tmp_path: Path) -> None:
        report_path = tmp_path / "r.json"
        result = runner.invoke(app, ["run", str(example), "--report", str(report_path)])
        assert result.exit_code == 0
        assert report_path.exists()

    def test_run_execution_failure_exit_code(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "commands": [
                        {"op": "new_part"},
                        {"op": "create_sketch"},
                        {"op": "draw_circle", "diameter": 5},
                        {"op": "cut_extrude"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["run", str(bad)])
        assert result.exit_code == 1
        # the report is still written for failed runs
        report = json.loads((tmp_path / "bad.json.report.json").read_text(encoding="utf-8"))
        assert report["success"] is False

    def test_solidworks_backend_unavailable_off_windows(self, example: Path) -> None:
        result = runner.invoke(app, ["run", str(example), "--backend", "solidworks"])
        assert result.exit_code == 1
        assert "mock" in result.output  # points the user at --backend mock
