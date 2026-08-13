"""End-to-end acceptance for v0.4: the bracket and flange drawing sheets.

Asserts the exact mock call plan — which is byte-identical to what the
COM backend executes on Windows (shared CallSpec builders).
"""

from pathlib import Path

import pytest

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_example(name: str):
    cmd_file, expanded = load_and_expand(EXAMPLES / name)
    backend = MockBackend()
    report = execute(expanded, backend, schema_version=cmd_file.schema_version)
    assert report.success, [r.error for r in report.results if r.error]
    return report, backend


def drawing_doc(report):
    return next(
        d for d in report.final_state["documents"] if d.get("kind") == "drawing"
    )


def drawing_calls(backend):
    """The call log from the first drawing-view call onward (the part
    build earlier in the log also selects EDGEs, in model space)."""
    methods = [c.method for c in backend.call_log]
    return backend.call_log[methods.index("CreateDrawViewFromModelView3"):]


class TestFlangeSheet:
    def test_twin_state(self) -> None:
        report, _ = run_example("flange_drawing.json")
        doc = drawing_doc(report)
        assert doc["sheet"] == "A4"
        assert doc["scale"] == "1:2"
        assert [v["name"] for v in doc["views"]] == ["front", "section_A", "iso"]
        dims = {d["name"]: d for d in doc["dimensions"]}
        assert dims["od_1"]["value"] == 120.0
        assert dims["od_2"]["value"] == 60.0
        assert dims["length"] == {
            "name": "length", "view": "section_A", "kind": "linear", "value": 40.0,
        }
        assert dims["step_1"]["value"] == 15.0
        assert dims["bore"]["prefix"] == "<MOD-DIAM>" and dims["bore"]["value"] == 30.0
        assert dims["callout_Cut-Extrude2"]["prefix"] == "6X "
        assert doc["notes"] == [
            "<MOD-DIAM>9 HOLES EQUALLY SPACED ON <MOD-DIAM>90 B.C."
        ]

    def test_title_block_properties_on_model(self) -> None:
        _, backend = run_example("flange_drawing.json")
        props = [c.args for c in backend.call_log if c.method == "AddCustomInfo3"]
        assert props == [
            ("", "Description", 30, "HOLLOW FLANGE"),
            ("", "DrawnBy", 30, "SW-Pilot"),
            ("", "DrawnDate", 30, "2026-08-13"),
        ]
        # set BEFORE the drawing document exists (they target the model)
        methods = [c.method for c in backend.call_log]
        assert methods.index("AddCustomInfo3") < methods.index(
            "CreateDrawViewFromModelView3"
        )

    def test_sheet_setup(self) -> None:
        _, backend = run_example("flange_drawing.json")
        new_docs = [c for c in backend.call_log if c.method == "NewDocument"]
        # part template new_part + drawing NewDocument with A4 paper enum 6
        assert new_docs[-1].args[1] == 6
        (sheet,) = [c for c in backend.call_log if c.method == "SetProperties"]
        assert sheet.target == "Sheet"
        assert sheet.args == (6, 6, 1.0, 2.0, False, pytest.approx(0.297), pytest.approx(0.210))

    def test_units_note(self) -> None:
        _, backend = run_example("flange_drawing.json")
        notes = [c.args[0] for c in backend.call_log if c.method == "InsertNote"]
        assert notes[0] == "DIMENSIONS IN MM"

    def test_front_view_call(self) -> None:
        report, backend = run_example("flange_drawing.json")
        views = [c for c in backend.call_log if c.method == "CreateDrawViewFromModelView3"]
        assert views[0].args[0] == "flange.SLDPRT"
        assert views[0].args[1] == "*Front"
        doc = drawing_doc(report)
        front = next(v for v in doc["views"] if v["name"] == "front")
        assert views[0].args[2] == pytest.approx(front["position"][0] * 1e-3)
        assert views[0].args[3] == pytest.approx(front["position"][1] * 1e-3)

    def test_section_sequence(self) -> None:
        _, backend = run_example("flange_drawing.json")
        methods = [c.method for c in backend.call_log]
        i = methods.index("ActivateView")
        assert backend.call_log[i].args == ("front",)
        assert methods[i + 1] == "CreateLine"
        assert methods[i + 2] == "CreateSectionViewAt5"
        sec = backend.call_log[i + 2]
        assert sec.args[3] == "A"
        assert sec.args[4:] == (0, None, 0.0)
        assert methods[i + 3] == "SetName2"
        assert backend.call_log[i + 3].args == ("section_A",)
        assert methods[i + 4] == "ActivateSheet"
        # the cutting line is vertical through the front view center
        line = backend.call_log[i + 1]
        assert line.args[0] == line.args[3]  # x1 == x2

    def test_iso_view_scale_override(self) -> None:
        _, backend = run_example("flange_drawing.json")
        views = [c for c in backend.call_log if c.method == "CreateDrawViewFromModelView3"]
        assert views[1].args[1] == "*Isometric"
        scales = [c for c in backend.call_log if c.method == "ScaleDecimal"]
        assert len(scales) == 1 and scales[0].value == pytest.approx(0.2)

    def test_bore_dimension_calls(self) -> None:
        report, backend = run_example("flange_drawing.json")
        doc = drawing_doc(report)
        sec = next(v for v in doc["views"] if v["name"] == "section_A")
        cx, cy = sec["position"]
        picks = [
            c.args
            for c in drawing_calls(backend)
            if c.method == "SelectByID2" and c.args[1] == "EDGE"
        ]
        # bore picks: at the section's x center, sheet y = center +- 15 * 0.5
        # (the front-family projection collapses x, the bore spans z)
        bore_ys = sorted(
            p[3] for p in picks if abs(p[2] - cx * 1e-3) < 1e-9
        )
        assert bore_ys[0] == pytest.approx((cy - 7.5) * 1e-3)
        assert bore_ys[-1] == pytest.approx((cy + 7.5) * 1e-3)
        prefixes = [
            c.args[1]
            for c in backend.call_log
            if c.method == "SetText" and c.args[0] == 1
        ]
        assert "6X " in prefixes  # bolt-hole callout count
        assert "<MOD-DIAM>" in prefixes  # bore linear dim reads as a diameter

    def test_save_drawing(self) -> None:
        _, backend = run_example("flange_drawing.json")
        saves = [c.args[0] for c in backend.call_log if c.method == "SaveAs3"]
        assert saves == ["flange.SLDPRT", "flange.SLDDRW"]


class TestBracketSheet:
    def test_twin_state(self) -> None:
        report, _ = run_example("bracket_drawing.json")
        doc = drawing_doc(report)
        assert doc["sheet"] == "A3" and doc["scale"] == "1:1"
        assert [v["name"] for v in doc["views"]] == ["front", "top", "right", "iso"]
        dims = {d["name"]: d for d in doc["dimensions"]}
        assert dims["envelope_width"]["value"] == 120.0
        assert dims["envelope_height"]["value"] == 80.0
        assert dims["thickness"]["value"] == 12.0 and dims["thickness"]["view"] == "right"
        cb = dims["callout_Cut-Extrude2"]
        assert cb["prefix"] == "4X " and cb["value"] == 6.6
        assert cb["below"] == "<HOLE-SPOT><MOD-DIAM>11 <HOLE-DEPTH>6"
        assert dims["pos_x_Cut-Extrude2"]["value"] == 15.0
        assert dims["pos_y_Cut-Extrude2"]["value"] == 15.0
        assert dims["callout_Cut-Extrude4"]["prefix"] == "3X "
        assert doc["notes"] == [
            "<MOD-DIAM>5 HOLES EQUALLY SPACED ON <MOD-DIAM>60 B.C.",
            "FILLETS R8 (4 PLCS)",
            "CHAMFERS 1 X 45<MOD-DEG> (4 PLCS)",
            "2X SLOT W10 X 40 C-C, PITCH 25",
        ]

    def test_projected_views_from_front(self) -> None:
        _, backend = run_example("bracket_drawing.json")
        unfolds = [c for c in backend.call_log if c.method == "CreateUnfoldedViewAt3"]
        assert len(unfolds) == 2
        parents = [
            c.args[0]
            for c in backend.call_log
            if c.method == "SelectByID2" and c.args[1] == "DRAWINGVIEW"
        ]
        assert parents == ["front", "front"]
        renames = [c.args[0] for c in backend.call_log if c.method == "SetName2"]
        assert renames == ["front", "top", "right", "iso"]

    def test_envelope_width_picks_on_outline(self) -> None:
        report, backend = run_example("bracket_drawing.json")
        doc = drawing_doc(report)
        front = next(v for v in doc["views"] if v["name"] == "front")
        cx, cy = front["position"]
        picks = [
            c.args
            for c in drawing_calls(backend)
            if c.method == "SelectByID2" and c.args[1] == "EDGE"
        ]
        # the first dimension is envelope_width: picks at x = center +- 60mm
        assert picks[0][2] == pytest.approx((cx - 60.0) * 1e-3)
        assert picks[0][3] == pytest.approx(cy * 1e-3)
        assert picks[0][5] is False  # starts a fresh selection
        assert picks[1][2] == pytest.approx((cx + 60.0) * 1e-3)
        assert picks[1][5] is True  # appended to the selection

    def test_no_dimension_dump(self) -> None:
        report, _ = run_example("bracket_drawing.json")
        doc = drawing_doc(report)
        # governing features only: exactly 7 dimensions for the bracket
        # (W/H/T, cb callout, 2 position dims, circular-hole callout —
        # slot size and pitch travel in the note block)
        assert len(doc["dimensions"]) == 7


class TestReportShape:
    def test_resolved_views_and_dimensions_attributed(self) -> None:
        report, _ = run_example("flange_drawing.json")
        by_op = {r.op: r for r in report.results}
        assert by_op["standard_views"].resolved == {"views": ["front"]}
        assert by_op["section_view"].resolved == {"views": ["section_A"]}
        sd = by_op["smart_dimensions"].resolved
        assert sd is not None and len(sd["dimensions"]) == 6

    def test_drawing_calls_attributed_to_commands(self) -> None:
        report, _ = run_example("flange_drawing.json")
        by_op = {r.op: r for r in report.results}
        assert by_op["create_drawing"].call_count > 0
        assert by_op["smart_dimensions"].call_count > 0
        assert by_op["save_drawing"].call_count == 1
