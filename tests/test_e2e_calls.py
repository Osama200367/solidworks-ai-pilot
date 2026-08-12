"""End-to-end acceptance: the plate example produces the exact COM call plan."""

from pathlib import Path

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_plate_with_holes_call_sequence() -> None:
    _, expanded = load_and_expand(EXAMPLES / "plate_with_holes.json")
    report = execute(expanded, MockBackend())
    assert report.success, [r.error for r in report.results if r.error]

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
        # extrude
        ("Model.SketchManager", "AddToDB"),
        ("Model", "ClearSelection2"),
        ("Model.FeatureManager", "FeatureExtrusion2"),
        # create_sketch (holes)
        ("Model.Extension", "SelectByID2"),
        ("Model.SketchManager", "InsertSketch"),
        ("Model.SketchManager", "AddToDB"),
        # 4x draw_circle
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        ("Model.SketchManager", "CreateCircleByRadius"),
        # cut_extrude
        ("Model.SketchManager", "AddToDB"),
        ("Model", "ClearSelection2"),
        ("Model.FeatureManager", "FeatureCut3"),
        # save_part
        ("Model", "SaveAs3"),
        # finalize
        ("Model", "ViewZoomtofit2"),
    ]


def test_plate_with_holes_final_state() -> None:
    _, expanded = load_and_expand(EXAMPLES / "plate_with_holes.json")
    report = execute(expanded, MockBackend())
    state = report.final_state
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
    _, macro_expanded = load_and_expand(EXAMPLES / "plate_with_holes.json")
    _, prim_expanded = load_and_expand(EXAMPLES / "plate_primitives.json")

    macro_backend = MockBackend()
    assert execute(macro_expanded, macro_backend).success
    prim_backend = MockBackend()
    assert execute(prim_expanded, prim_backend).success

    def feature_calls(backend: MockBackend) -> list:
        return [
            (c.method, c.args)
            for c in backend.call_log
            if c.method in ("FeatureExtrusion2", "FeatureCut3", "CreateCenterRectangle")
        ]

    assert feature_calls(macro_backend) == feature_calls(prim_backend)

    # circle sets match regardless of ordering
    def circles(backend: MockBackend) -> set:
        return {c.args for c in backend.call_log if c.method == "CreateCircleByRadius"}

    assert circles(macro_backend) == circles(prim_backend)
