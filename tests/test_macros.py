"""Macro expansion tests (loader + macros, tracker-backed)."""

import pytest

from swpilot.commands.loader import CommandFileError, expand_commands, parse_command_data
from swpilot.commands.schema import (
    CreateAxis,
    CreatePlane,
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    Extrude,
    NewPart,
)


def expand(*commands: dict) -> list:
    cf = parse_command_data({"schema_version": "0.2", "commands": list(commands)})
    return expand_commands(list(cf.commands))


PLATE = {"op": "create_plate", "width": 100, "height": 50, "thickness": 10}


class TestCreatePlate:
    def test_expansion_sequence(self) -> None:
        out = expand(PLATE)
        cmds = [ec.command for ec in out]
        assert isinstance(cmds[0], NewPart)
        assert isinstance(cmds[1], CreateSketch) and cmds[1].plane == "front"
        assert isinstance(cmds[2], DrawRectangle)
        assert cmds[2].width == 100 and cmds[2].height == 50 and cmds[2].center == (0.0, 0.0)
        assert isinstance(cmds[3], Extrude) and cmds[3].depth == 10
        assert len(cmds) == 4

    def test_provenance_recorded(self) -> None:
        out = expand(PLATE)
        assert all(ec.source_op == "create_plate" for ec in out)
        assert all(ec.source_index == 0 for ec in out)
        assert [ec.expansion_step for ec in out] == [0, 1, 2, 3]

    def test_custom_plane_propagates(self) -> None:
        out = expand({**PLATE, "plane": "top"})
        sketch = out[1].command
        assert isinstance(sketch, CreateSketch) and sketch.plane == "top"


class TestAddCornerHoles:
    def test_expansion_sequence_and_positions(self) -> None:
        out = expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 10})
        cmds = [ec.command for ec in out]
        # 4 for the plate, then sketch + 4 circles + cut
        assert len(cmds) == 10
        assert isinstance(cmds[4], CreateSketch) and cmds[4].plane == "front"
        circles = cmds[5:9]
        assert all(isinstance(c, DrawCircle) and c.diameter == 8 for c in circles)
        centers = {c.center for c in circles if isinstance(c, DrawCircle)}
        assert centers == {(-40.0, -15.0), (40.0, -15.0), (-40.0, 15.0), (40.0, 15.0)}
        cut = cmds[9]
        assert isinstance(cut, CutExtrude) and cut.through_all is True

    def test_without_document_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="no document is open"):
            expand({"op": "add_corner_holes", "diameter": 8, "margin": 10})

    def test_without_boss_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="no boss feature"):
            expand({"op": "new_part"}, {"op": "add_corner_holes", "diameter": 8, "margin": 10})

    def test_non_rectangular_boss_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="single rectangle"):
            expand(
                {"op": "new_part"},
                {"op": "create_sketch"},
                {"op": "draw_circle", "diameter": 60},
                {"op": "extrude", "depth": 10},
                {"op": "add_corner_holes", "diameter": 8, "margin": 10},
            )

    def test_margin_not_exceeding_radius_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="exceed the hole radius"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 4})

    def test_margin_too_large_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="too large"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 25})

    def test_overlapping_holes_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="overlap or touch"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 12, "margin": 20})

    def test_error_names_offending_command_index(self) -> None:
        with pytest.raises(CommandFileError, match=r"commands\[1\]"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 4})

    def test_offcenter_plate_offsets_holes(self) -> None:
        out = expand(
            {"op": "new_part"},
            {"op": "create_sketch"},
            {"op": "draw_rectangle", "center": [10, 5], "width": 100, "height": 50},
            {"op": "extrude", "depth": 10},
            {"op": "add_corner_holes", "diameter": 8, "margin": 10},
        )
        centers = {
            c.command.center for c in out if isinstance(c.command, DrawCircle)
        }
        assert centers == {(-30.0, -10.0), (50.0, -10.0), (-30.0, 20.0), (50.0, 20.0)}


class TestHole:
    def test_counterbore_expansion(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "type": "counterbore", "standard": "M6", "at": [[20, 0]]},
        )
        cmds = [ec.command for ec in out]
        # plate(4) + plane + (sketch + circle + blind cut) + (sketch + circle + through cut)
        assert len(cmds) == 11
        plane = cmds[4]
        assert isinstance(plane, CreatePlane)
        assert plane.name == "SWPilot_Plane1"
        assert plane.offset_from == "front" and plane.distance == 10.0
        assert isinstance(cmds[5], CreateSketch) and cmds[5].plane == "SWPilot_Plane1"
        cb_circle = cmds[6]
        assert isinstance(cb_circle, DrawCircle) and cb_circle.diameter == 11.0
        cb_cut = cmds[7]
        assert isinstance(cb_cut, CutExtrude)
        assert cb_cut.depth == 6.0 and cb_cut.reverse is True
        hole_circle = cmds[9]
        assert isinstance(hole_circle, DrawCircle) and hole_circle.diameter == 6.6
        through = cmds[10]
        assert isinstance(through, CutExtrude)
        assert through.through_all is True and through.reverse is True

    def test_countersink_cone_depth_math(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "type": "countersink", "standard": "M6", "at": [[0, 0]]},
        )
        cuts = [c.command for c in out if isinstance(c.command, CutExtrude)]
        cone = cuts[0]
        # (12.6 - 6.6) / 2 / tan(45 deg) = 3.0
        assert cone.depth == pytest.approx(3.0)
        assert cone.draft_angle == pytest.approx(45.0)
        assert cone.reverse is True

    def test_simple_hole_reuses_existing_plane(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "type": "counterbore", "standard": "M6", "at": [[20, 0]]},
            {"op": "hole", "at": [[-20, 0]], "diameter": 5},
        )
        planes = [c.command for c in out if isinstance(c.command, CreatePlane)]
        assert len(planes) == 1  # second hole reuses SWPilot_Plane1

    def test_hole_on_named_plane(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "at": [[0, 0]], "diameter": 5, "on": "front"},
        )
        cuts = [c.command for c in out if isinstance(c.command, CutExtrude)]
        # drilling from the bottom surface: cut runs along +normal
        assert cuts[-1].reverse is False
        planes = [c.command for c in out if isinstance(c.command, CreatePlane)]
        assert planes == []

    def test_hole_on_face_ref(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "at": [[0, 0]], "diameter": 5, "on": {"facing": "+z"}},
        )
        planes = [c.command for c in out if isinstance(c.command, CreatePlane)]
        assert len(planes) == 1 and planes[0].distance == 10.0

    def test_hole_without_material_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="no boss feature"):
            expand({"op": "new_part"}, {"op": "hole", "at": [[0, 0]], "diameter": 5})


class TestSketchOnFace:
    def test_face_ref_becomes_offset_plane(self) -> None:
        out = expand(PLATE, {"op": "create_sketch", "on": {"facing": "+z"}})
        cmds = [ec.command for ec in out]
        plane = cmds[4]
        assert isinstance(plane, CreatePlane) and plane.distance == 10.0
        sketch = cmds[5]
        assert isinstance(sketch, CreateSketch) and sketch.plane == plane.name

    def test_side_face_uses_matching_family(self) -> None:
        out = expand(PLATE, {"op": "create_sketch", "on": {"facing": "+x"}})
        plane = out[4].command
        assert isinstance(plane, CreatePlane)
        assert plane.offset_from == "right" and plane.distance == 50.0

    def test_bottom_face_reuses_standard_plane(self) -> None:
        out = expand(PLATE, {"op": "create_sketch", "on": {"facing": "-z"}})
        cmds = [ec.command for ec in out]
        assert len(cmds) == 5  # no CreatePlane needed: position 0 = front plane
        sketch = cmds[4]
        assert isinstance(sketch, CreateSketch) and sketch.plane == "front"


class TestPatternAxisInsertion:
    def test_missing_axis_auto_created(self) -> None:
        out = expand(
            PLATE,
            {"op": "hole", "at": [[-30, 0]], "diameter": 8},
            {
                "op": "linear_pattern",
                "features": ["Cut-Extrude1"],
                "direction": "x",
                "spacing": 30,
                "count": 3,
            },
        )
        axes = [c.command for c in out if isinstance(c.command, CreateAxis)]
        assert [a.axis for a in axes] == ["x"]

    def test_existing_axis_not_duplicated(self) -> None:
        out = expand(
            PLATE,
            {"op": "create_axis", "axis": "x"},
            {"op": "hole", "at": [[-30, 0]], "diameter": 8},
            {
                "op": "linear_pattern",
                "features": ["Cut-Extrude1"],
                "direction": "-x",
                "spacing": 30,
                "count": 2,
            },
        )
        axes = [c.command for c in out if isinstance(c.command, CreateAxis)]
        assert len(axes) == 1


class TestPrimitivesPassThrough:
    def test_primitives_untouched(self) -> None:
        out = expand({"op": "new_part"}, {"op": "create_sketch", "plane": "top"})
        assert [ec.command.op for ec in out] == ["new_part", "create_sketch"]
        assert all(ec.expansion_step is None for ec in out)
        assert [ec.source_index for ec in out] == [0, 1]

    def test_geometric_error_caught_at_expansion(self) -> None:
        # v0.2: tracker validation runs during expansion, so `validate`
        # catches cuts outside material with no backend involved.
        with pytest.raises(CommandFileError, match="miss the part entirely"):
            expand(
                PLATE,
                {"op": "create_sketch"},
                {"op": "draw_circle", "center": [80, 0], "diameter": 8},
                {"op": "cut_extrude"},
            )
