"""Command schema validation tests."""

import pytest
from pydantic import ValidationError

from swpilot.commands.schema import (
    Chamfer,
    CircularPattern,
    CommandFile,
    CreatePlane,
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    DrawSlot,
    Fillet,
    Hole,
    LinearPattern,
    SavePart,
)


def make_file(*commands: dict) -> dict:
    return {"schema_version": "0.2", "commands": list(commands)}


class TestCommandFile:
    def test_minimal_valid_file(self) -> None:
        cf = CommandFile.model_validate(make_file({"op": "new_part"}))
        assert cf.schema_version == "0.2"
        assert cf.commands[0].op == "new_part"

    def test_v01_files_still_accepted(self) -> None:
        cf = CommandFile.model_validate(
            {"schema_version": "0.1", "commands": [{"op": "new_part"}]}
        )
        assert cf.schema_version == "0.1"

    def test_unknown_op_rejected(self) -> None:
        with pytest.raises(ValidationError, match="new_prat|discriminator|tag"):
            CommandFile.model_validate(make_file({"op": "new_prat"}))

    def test_missing_op_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandFile.model_validate(make_file({"width": 100}))

    def test_wrong_schema_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandFile.model_validate({"schema_version": "9.9", "commands": [{"op": "new_part"}]})

    def test_empty_command_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandFile.model_validate({"schema_version": "0.2", "commands": []})

    def test_extra_command_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandFile.model_validate(make_file({"op": "new_part", "color": "red"}))


class TestDimensions:
    @pytest.mark.parametrize("bad", [0, -5, "nan", "inf", True])
    def test_rectangle_dimensions_must_be_positive_finite(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            DrawRectangle.model_validate({"op": "draw_rectangle", "width": bad, "height": 10})

    def test_bool_coordinate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="booleans"):
            DrawCircle.model_validate(
                {"op": "draw_circle", "center": [True, 0], "diameter": 5}
            )

    def test_center_defaults_to_origin(self) -> None:
        c = DrawCircle.model_validate({"op": "draw_circle", "diameter": 5})
        assert c.center == (0.0, 0.0)


class TestCutExtrude:
    def test_default_is_through_all(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude"})
        assert c.through_all is True and c.depth is None and c.reverse is False

    def test_depth_alone_implies_blind(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude", "depth": 5})
        assert c.through_all is False and c.depth == 5

    def test_null_depth_means_through_all(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude", "depth": None})
        assert c.through_all is True

    def test_through_all_with_depth_rejected(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            CutExtrude.model_validate({"op": "cut_extrude", "through_all": True, "depth": 5})

    def test_blind_without_depth_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires 'depth'"):
            CutExtrude.model_validate({"op": "cut_extrude", "through_all": False})

    def test_draft_requires_blind(self) -> None:
        with pytest.raises(ValidationError, match="blind cut"):
            CutExtrude.model_validate({"op": "cut_extrude", "draft_angle": 45})
        c = CutExtrude.model_validate({"op": "cut_extrude", "depth": 3, "draft_angle": 45})
        assert c.draft_angle == 45

    @pytest.mark.parametrize("bad", [0, 90, -10])
    def test_draft_angle_range(self, bad: float) -> None:
        with pytest.raises(ValidationError):
            CutExtrude.model_validate({"op": "cut_extrude", "depth": 3, "draft_angle": bad})


class TestSelectors:
    def test_fillet_named_selector(self) -> None:
        f = Fillet.model_validate(
            {"op": "fillet", "radius": 2, "edges": {"select": "vertical_corners"}}
        )
        assert f.edges.select == "vertical_corners"  # type: ignore[union-attr]

    def test_fillet_near_point(self) -> None:
        f = Fillet.model_validate(
            {"op": "fillet", "radius": 2, "edges": {"near_point": [50, 25, 5]}}
        )
        assert f.edges.near_point == (50.0, 25.0, 5.0)  # type: ignore[union-attr]

    def test_unknown_selector_group_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Fillet.model_validate({"op": "fillet", "radius": 2, "edges": {"select": "sides"}})

    def test_chamfer_angle_default_and_range(self) -> None:
        c = Chamfer.model_validate(
            {"op": "chamfer", "distance": 1, "edges": {"select": "top_loop"}}
        )
        assert c.angle == 45.0
        with pytest.raises(ValidationError):
            Chamfer.model_validate(
                {"op": "chamfer", "distance": 1, "angle": 90, "edges": {"select": "all"}}
            )


class TestCreateSketchTargets:
    def test_plane_default(self) -> None:
        assert CreateSketch.model_validate({"op": "create_sketch"}).plane == "front"

    def test_face_ref(self) -> None:
        s = CreateSketch.model_validate({"op": "create_sketch", "on": {"facing": "+z"}})
        assert s.on is not None and s.on.facing == "+z"

    def test_plane_and_on_together_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            CreateSketch.model_validate(
                {"op": "create_sketch", "plane": "top", "on": {"facing": "+z"}}
            )


class TestHoleSchema:
    def test_standard_fills_dimensions(self) -> None:
        h = Hole.model_validate(
            {"op": "hole", "type": "counterbore", "standard": "M6", "at": [[0, 0]]}
        )
        assert h.diameter == 6.6 and h.cb_diameter == 11.0 and h.cb_depth == 6.0

    def test_explicit_fields_win_over_standard(self) -> None:
        h = Hole.model_validate(
            {
                "op": "hole",
                "type": "counterbore",
                "standard": "M6",
                "cb_depth": 8.5,
                "at": [[0, 0]],
            }
        )
        assert h.cb_depth == 8.5 and h.cb_diameter == 11.0

    def test_unknown_standard_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown standard"):
            Hole.model_validate({"op": "hole", "standard": "M7", "at": [[0, 0]]})

    def test_missing_diameter_rejected(self) -> None:
        with pytest.raises(ValidationError, match="diameter"):
            Hole.model_validate({"op": "hole", "at": [[0, 0]]})

    def test_cb_fields_require_counterbore_type(self) -> None:
        with pytest.raises(ValidationError, match="only valid with type=counterbore"):
            Hole.model_validate(
                {"op": "hole", "diameter": 5, "cb_diameter": 10, "cb_depth": 3, "at": [[0, 0]]}
            )

    def test_cb_diameter_must_exceed_hole(self) -> None:
        with pytest.raises(ValidationError, match="must exceed"):
            Hole.model_validate(
                {
                    "op": "hole",
                    "type": "counterbore",
                    "diameter": 6,
                    "cb_diameter": 5,
                    "cb_depth": 3,
                    "at": [[0, 0]],
                }
            )

    def test_countersink_diameter_must_exceed_hole(self) -> None:
        with pytest.raises(ValidationError, match="must exceed"):
            Hole.model_validate(
                {
                    "op": "hole",
                    "type": "countersink",
                    "diameter": 6,
                    "cs_diameter": 5,
                    "at": [[0, 0]],
                }
            )

    def test_at_requires_positions(self) -> None:
        with pytest.raises(ValidationError):
            Hole.model_validate({"op": "hole", "diameter": 5, "at": []})


class TestPatternSchema:
    def test_linear_pattern_defaults(self) -> None:
        p = LinearPattern.model_validate(
            {
                "op": "linear_pattern",
                "features": ["Cut-Extrude1"],
                "direction": "-y",
                "spacing": 10,
                "count": 3,
            }
        )
        assert p.direction2 is None

    def test_count_minimum(self) -> None:
        with pytest.raises(ValidationError):
            LinearPattern.model_validate(
                {
                    "op": "linear_pattern",
                    "features": ["F"],
                    "direction": "x",
                    "spacing": 10,
                    "count": 1,
                }
            )

    def test_circular_pattern_angle_range(self) -> None:
        with pytest.raises(ValidationError):
            CircularPattern.model_validate(
                {
                    "op": "circular_pattern",
                    "features": ["F"],
                    "axis": "z",
                    "count": 4,
                    "total_angle": 400,
                }
            )


class TestOtherCommands:
    def test_draw_slot_degenerate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="coincide"):
            DrawSlot.model_validate(
                {"op": "draw_slot", "start": [5, 5], "end": [5, 5], "width": 4}
            )

    def test_create_plane_requires_name(self) -> None:
        with pytest.raises(ValidationError):
            CreatePlane.model_validate({"op": "create_plane", "distance": 10})

    def test_save_part_extension(self) -> None:
        assert SavePart.model_validate({"op": "save_part", "path": "a.SLDPRT"}).path == "a.SLDPRT"
        with pytest.raises(ValidationError, match="SLDPRT"):
            SavePart.model_validate({"op": "save_part", "path": "a.step"})
