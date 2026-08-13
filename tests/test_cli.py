"""CLI tests via typer's CliRunner."""

import json
import shutil
import sys
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
        assert "no document is open" in result.output

    def test_geometric_error_caught_at_validate_time(self, tmp_path: Path) -> None:
        # v0.2: validate runs the twin, so cuts outside material fail here.
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": "0.2",
                    "commands": [
                        {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
                        {"op": "create_sketch"},
                        {"op": "draw_circle", "center": [80, 0], "diameter": 8},
                        {"op": "cut_extrude"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 2
        assert "miss the part entirely" in result.output


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

    def test_run_invalid_geometry_fails_at_validation(self, tmp_path: Path) -> None:
        # v0.2: the loader's tracker pass catches geometric errors before a
        # backend even exists, so `run` on such a file exits 2 (invalid
        # file) and writes no report.
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
        assert result.exit_code == 2
        assert "no solid material" in result.output
        assert not (tmp_path / "bad.json.report.json").exists()

    def test_solidworks_backend_unavailable_without_pywin32(
        self, example: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A None entry in sys.modules makes the import raise ImportError,
        # so this exercises the friendly-error path on every platform —
        # including Windows machines where pywin32 IS installed.
        monkeypatch.setitem(sys.modules, "swpilot.backends.solidworks.com_backend", None)
        result = runner.invoke(app, ["run", str(example), "--backend", "solidworks"])
        assert result.exit_code == 1
        assert "mock" in result.output  # points the user at --backend mock

    def test_mock_warns_when_solidworks_options_given(self, example: Path) -> None:
        result = runner.invoke(app, ["run", str(example), "--template", "X.prtdot"])
        assert result.exit_code == 0
        assert "ignored by the mock backend" in result.output

    def test_non_utf8_file_exits_2(self, tmp_path: Path) -> None:
        bad = tmp_path / "utf16.json"
        bad.write_bytes(
            json.dumps({"schema_version": "0.1", "commands": [{"op": "new_part"}]}).encode(
                "utf-16"
            )
        )
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 2
        assert "not UTF-8" in result.output

    def test_utf8_bom_file_accepted(self, tmp_path: Path) -> None:
        good = tmp_path / "bom.json"
        good.write_bytes(
            b"\xef\xbb\xbf"
            + json.dumps({"schema_version": "0.1", "commands": [{"op": "new_part"}]}).encode(
                "utf-8"
            )
        )
        result = runner.invoke(app, ["validate", str(good)])
        assert result.exit_code == 0, result.output
