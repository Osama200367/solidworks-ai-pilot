"""Macro expansion tests (loader + macros)."""

import pytest

from swpilot.commands.loader import CommandFileError, expand_commands, parse_command_data
from swpilot.commands.schema import (
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    Extrude,
    NewPart,
)


def expand(*commands: dict) -> list:
    cf = parse_command_data({"schema_version": "0.1", "commands": list(commands)})
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

    def test_holes_sketch_follows_plate_plane(self) -> None:
        out = expand(
            {**PLATE, "plane": "right"}, {"op": "add_corner_holes", "diameter": 8, "margin": 10}
        )
        sketch = out[4].command
        assert isinstance(sketch, CreateSketch) and sketch.plane == "right"

    def test_without_plate_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="no preceding create_plate"):
            expand({"op": "add_corner_holes", "diameter": 8, "margin": 10})

    def test_margin_not_exceeding_radius_rejected(self) -> None:
        # margin == radius means the hole is tangent to the plate edge
        with pytest.raises(CommandFileError, match="exceed the hole radius"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 4})

    def test_margin_too_large_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="too large"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 25})

    def test_overlapping_holes_rejected(self) -> None:
        # 50mm side, margin 20 -> centers 10mm apart, diameter 12 -> overlap
        with pytest.raises(CommandFileError, match="overlap or touch"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 12, "margin": 20})

    def test_touching_holes_rejected(self) -> None:
        # 50mm side, margin 20 -> centers 10mm apart, diameter 10 -> tangent
        with pytest.raises(CommandFileError, match="overlap or touch"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 10, "margin": 20})

    def test_error_names_offending_command_index(self) -> None:
        with pytest.raises(CommandFileError, match=r"commands\[1\]"):
            expand(PLATE, {"op": "add_corner_holes", "diameter": 8, "margin": 4})


class TestPrimitivesPassThrough:
    def test_primitives_untouched(self) -> None:
        out = expand({"op": "new_part"}, {"op": "create_sketch", "plane": "top"})
        assert [ec.command.op for ec in out] == ["new_part", "create_sketch"]
        assert all(ec.expansion_step is None for ec in out)
        assert [ec.source_index for ec in out] == [0, 1]
