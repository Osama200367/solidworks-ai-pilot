"""Executor behavior tests: fail-fast, attribution, report structure."""

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import expand_commands, parse_command_data
from swpilot.executor import execute


def run_commands(*commands: dict):
    cf = parse_command_data({"schema_version": "0.1", "commands": list(commands)})
    expanded = expand_commands(list(cf.commands))
    return execute(expanded, MockBackend())


GOOD = [
    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
    {"op": "add_corner_holes", "diameter": 8, "margin": 10},
]


class TestHappyPath:
    def test_success_report(self) -> None:
        report = run_commands(*GOOD)
        assert report.success is True
        assert all(r.status == "ok" for r in report.results)
        assert report.backend == "mock"

    def test_every_call_attributed(self) -> None:
        report = run_commands(*GOOD)
        # finalize()'s zoom-to-fit call is logged but attributed to no command
        attributed = sum(r.call_count for r in report.results)
        assert attributed == len(report.call_log) - 1

    def test_report_dict_shape(self) -> None:
        d = run_commands(*GOOD).to_dict()
        assert d["schema_version"] == "0.1"
        assert d["success"] is True
        assert d["commands_total"] == d["commands_ok"] == 10
        assert isinstance(d["call_log"], list)
        assert d["final_state"]["units"] == "mm"
        first = d["commands"][0]
        assert first["op"] == "new_part"
        assert first["expanded_from"] == "create_plate"


class TestFailFast:
    def test_error_stops_execution_and_skips_rest(self) -> None:
        report = run_commands(
            {"op": "new_part"},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": 5},
            {"op": "cut_extrude"},  # fails: no solid to cut
            {"op": "save_part", "path": "x.SLDPRT"},
        )
        assert report.success is False
        statuses = [r.status for r in report.results]
        assert statuses == ["ok", "ok", "ok", "error", "skipped"]
        assert "no solid material" in (report.results[3].error or "")

    def test_failed_run_still_reports_state(self) -> None:
        report = run_commands(
            {"op": "new_part"},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": 5},
            {"op": "cut_extrude"},
        )
        assert report.final_state["sketches"]

    def test_no_finalize_call_after_failure(self) -> None:
        report = run_commands(
            {"op": "new_part"},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": 5},
            {"op": "cut_extrude"},
        )
        assert not any(c["method"] == "ViewZoomtofit2" for c in report.call_log)


class TestWarnings:
    def test_warnings_attached_to_their_command(self) -> None:
        report = run_commands({"op": "new_part"}, {"op": "save_part", "path": "e.SLDPRT"})
        save_result = report.results[1]
        assert any("no solid geometry" in w for w in save_result.warnings)
