# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Executor behavior tests: fail-fast, attribution, report structure.

Loader expansion already tracker-validates whole files, so building an
*invalid* run that reaches the executor requires bypassing the loader:
`raw()` wraps primitives as ExpandedCommand directly, exactly like a
caller driving execute() without load_and_expand.
"""

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import ExpandedCommand, expand_commands, parse_command_data
from swpilot.commands.schema import CommandFile
from swpilot.executor import execute


def raw(*commands: dict) -> list[ExpandedCommand]:
    cf = CommandFile.model_validate({"schema_version": "0.2", "commands": list(commands)})
    return [
        ExpandedCommand(command=c, source_index=i, source_op=c.op)  # type: ignore[arg-type]
        for i, c in enumerate(cf.commands)
    ]


def run_commands(*commands: dict):
    cf = parse_command_data({"schema_version": "0.2", "commands": list(commands)})
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
        assert d["schema_version"] == "0.5"
        assert d["success"] is True
        assert d["commands_total"] == d["commands_ok"] == 10
        assert isinstance(d["call_log"], list)
        part_doc = d["final_state"]["documents"][0]
        assert part_doc["kind"] == "part" and part_doc["units"] == "mm"
        assert d["backend_state"]["backend"] == "mock"
        first = d["commands"][0]
        assert first["op"] == "new_part"
        assert first["expanded_from"] == "create_plate"

    def test_resolved_selections_reported(self) -> None:
        report = run_commands(
            *GOOD, {"op": "fillet", "radius": 5, "edges": {"select": "vertical_corners"}}
        )
        fillet_results = [r for r in report.results if r.op == "fillet"]
        assert len(fillet_results) == 1
        resolved = fillet_results[0].resolved
        assert resolved is not None
        assert len(resolved["edges"]) == 4
        assert all(e["feature"] == "Boss-Extrude1" for e in resolved["edges"])


BAD_RAW = [
    {"op": "new_part"},
    {"op": "create_sketch"},
    {"op": "draw_circle", "diameter": 5},
    {"op": "cut_extrude"},  # fails at the executor's tracker: no solid
    {"op": "save_part", "path": "x.SLDPRT"},
]


class TestFailFast:
    def test_error_stops_execution_and_skips_rest(self) -> None:
        report = execute(raw(*BAD_RAW), MockBackend())
        assert report.success is False
        statuses = [r.status for r in report.results]
        assert statuses == ["ok", "ok", "ok", "error", "skipped"]
        assert "no solid material" in (report.results[3].error or "")

    def test_failed_command_logs_no_calls(self) -> None:
        # Tracker validation runs before backend dispatch, so a rejected
        # command contributes nothing to the call log — on Windows this
        # means SolidWorks is never touched by an invalid command.
        report = execute(raw(*BAD_RAW), MockBackend())
        assert report.results[3].call_count == 0

    def test_no_finalize_call_after_failure(self) -> None:
        report = execute(raw(*BAD_RAW), MockBackend())
        assert not any(c["method"] == "ViewZoomtofit2" for c in report.call_log)


class TestWarnings:
    def test_warnings_attached_to_their_command(self) -> None:
        report = run_commands({"op": "new_part"}, {"op": "save_part", "path": "e.SLDPRT"})
        save_result = report.results[1]
        assert any("no solid geometry" in w for w in save_result.warnings)

    def test_finalize_warnings_on_last_result(self) -> None:
        report = run_commands(
            {"op": "new_part"},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": 5},
        )
        assert any("unconsumed sketch" in w for w in report.results[-1].warnings)


class TestExceptionContainment:
    def test_unexpected_exception_recorded_as_error(self) -> None:
        class ExplodingBackend(MockBackend):
            def extrude(self, depth: float, reverse: bool, name: str) -> None:
                raise KeyError("simulated backend bug")

        cf = parse_command_data(
            {
                "schema_version": "0.2",
                "commands": [{"op": "create_plate", "width": 10, "height": 10, "thickness": 2}],
            }
        )
        report = execute(expand_commands(list(cf.commands)), ExplodingBackend())
        assert report.success is False
        failing = [r for r in report.results if r.status == "error"]
        assert len(failing) == 1
        assert "KeyError" in (failing[0].error or "")

    def test_finalize_failure_recorded_not_raised(self) -> None:
        class FailingFinalize(MockBackend):
            def finalize(self) -> None:
                raise RuntimeError("zoom failed")

        cf = parse_command_data(
            {"schema_version": "0.2", "commands": [{"op": "new_part"}]}
        )
        report = execute(expand_commands(list(cf.commands)), FailingFinalize())
        assert report.success is False
        assert report.finalize_error is not None
        assert "zoom failed" in report.finalize_error
        assert "finalize_error" in report.to_dict()
