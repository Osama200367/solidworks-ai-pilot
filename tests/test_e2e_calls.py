# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""End-to-end acceptance: examples produce the exact COM call plans."""

from pathlib import Path

import pytest

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_example(name: str):
    _, expanded = load_and_expand(EXAMPLES / name)
    backend = MockBackend()
    report = execute(expanded, backend)
    assert report.success, [r.error for r in report.results if r.error]
    return report, backend


def test_plate_with_holes_call_sequence() -> None:
    report, _ = run_example("plate_with_holes.json")
    methods = [(c["target"], c["method"]) for c in report.call_log]
    assert methods == [
        # new_part
        ("App", "GetUserPreferenceStringValue"),
        ("App", "NewDocument"),
        # create_sketch (base)
        ("Model.Extension", "SelectByID2"),
        ("Model.SketchManager", "InsertSketch"),
        ("Model.SketchManager", "AddToDB"),
        # draw_rectangle
        ("Model.SketchManager", "CreateCenterRectangle"),
        # extrude (+ rename to twin name)
        ("Model.SketchManager", "AddToDB"),
        ("Model", "ClearSelection2"),
        ("Model.FeatureManager", "FeatureExtrusion2"),
        ("LastFeature", "Name"),
        # create_sketch (holes)
        ("Model.Extension", "SelectByID2"),
        ("Model.SketchManager", "InsertSketch"),
        ("Model.SketchManager", "AddToDB"),
        # 4x draw_circle
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        # cut_extrude (+ rename)
        ("Model.SketchManager", "AddToDB"),
        ("Model", "ClearSelection2"),
        ("Model.FeatureManager", "FeatureCut3"),
        ("LastFeature", "Name"),
        # save_part
        ("Model", "SaveAs3"),
        # finalize
        ("Model", "ViewZoomtofit2"),
    ]


def test_plate_with_holes_final_state() -> None:
    report, _ = run_example("plate_with_holes.json")
    state = report.final_state["documents"][0]
    assert [f["name"] for f in state["features"]] == ["Boss-Extrude1", "Cut-Extrude1"]
    holes = state["sketches"][1]["entities"]
    assert len(holes) == 4
    assert {tuple(h["center"]) for h in holes} == {
        (-40.0, -15.0),
        (40.0, -15.0),
        (-40.0, 15.0),
        (40.0, 15.0),
    }
    assert state["saved_to"] == ["plate_100x50.SLDPRT"]


def test_primitives_example_matches_macro_expansion_calls() -> None:
    """The raw-primitive example must produce the same feature calls as the macro."""
    _, macro_backend = run_example("plate_with_holes.json")
    _, prim_backend = run_example("plate_primitives.json")

    def feature_calls(backend: MockBackend) -> list:
        return [
            (c.method, c.args)
            for c in backend.call_log
            if c.method in ("FeatureExtrusion2", "FeatureCut3", "CreateCenterRectangle")
        ]

    assert feature_calls(macro_backend) == feature_calls(prim_backend)

    def circles(backend: MockBackend) -> set:
        return {c.args for c in backend.call_log if c.method == "CreateCircleByRadius"}

    assert circles(macro_backend) == circles(prim_backend)


class TestBracket:
    """The v0.2 acceptance example: every new command family in one part."""

    def test_succeeds_with_expected_features(self) -> None:
        report, _ = run_example("bracket.json")
        doc = report.final_state["documents"][0]
        assert [f["name"] for f in doc["features"]] == [
            "Boss-Extrude1",
            "Fillet1",
            "Chamfer1",
            "Cut-Extrude1",
            "Cut-Extrude2",
            "Cut-Extrude3",
            "LPattern1",
            "Cut-Extrude4",
            "CirPattern1",
        ]
        assert doc["axes"] == ["SWPilot_Axis_Y", "SWPilot_Axis_Z"]
        assert "SWPilot_Plane1" in doc["planes"]

    def test_fillet_selects_corner_edges_in_meters(self) -> None:
        _, backend = run_example("bracket.json")
        edge_picks = [
            c.args for c in backend.call_log if c.method == "SelectByID2" and c.args[1] == "EDGE"
        ]
        # first four edge selections belong to the fillet: plate corners at
        # (±60, ±40, 6) mm -> (±0.06, ±0.04, 0.006) m
        fillet_picks = {(a[2], a[3], a[4]) for a in edge_picks[:4]}
        assert fillet_picks == {
            (-0.06, -0.04, 0.006),
            (0.06, -0.04, 0.006),
            (0.06, 0.04, 0.006),
            (-0.06, 0.04, 0.006),
        }

    def test_counterbore_cut_is_reversed_blind(self) -> None:
        _, backend = run_example("bracket.json")
        cuts = [c.args for c in backend.call_log if c.method == "FeatureCut3"]
        # first cut: counterbore, blind 6mm, direction reversed (Dir=True)
        cb = cuts[0]
        assert cb[2] is True  # Dir: reversed
        assert cb[3] == 0  # T1: blind
        assert cb[5] == pytest.approx(0.006)  # D1 = 6mm

    def test_pattern_calls_reference_renamed_features(self) -> None:
        _, backend = run_example("bracket.json")
        seed_selects = [
            c.args[0]
            for c in backend.call_log
            if c.method == "SelectByID2" and c.args[1] == "BODYFEATURE"
        ]
        assert seed_selects == ["Cut-Extrude3", "Cut-Extrude4"]
        axis_selects = [
            c.args[0]
            for c in backend.call_log
            if c.method == "SelectByID2" and c.args[1] == "AXIS"
        ]
        assert axis_selects == ["SWPilot_Axis_Y", "SWPilot_Axis_Z"]

    def test_offset_plane_created_once_and_reused(self) -> None:
        _, backend = run_example("bracket.json")
        ref_planes = [c for c in backend.call_log if c.method == "InsertRefPlane"]
        assert len(ref_planes) == 1
        assert ref_planes[0].args[1] == pytest.approx(0.012)  # 12mm in meters

    def test_every_feature_renamed(self) -> None:
        _, backend = run_example("bracket.json")
        renames = [c.value for c in backend.call_log if c.target == "LastFeature"]
        assert renames == [
            "Boss-Extrude1",
            "Fillet1",
            "Chamfer1",
            "SWPilot_Plane1",
            "Cut-Extrude1",
            "Cut-Extrude2",
            "Cut-Extrude3",
            "SWPilot_Axis_Y",
            "LPattern1",
            "Cut-Extrude4",
            "SWPilot_Axis_Z",
            "CirPattern1",
        ]
