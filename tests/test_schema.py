"""Command schema validation tests."""

import pytest
from pydantic import ValidationError

from swpilot.commands.schema import (
    CommandFile,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    SavePart,
)


def make_file(*commands: dict) -> dict:
    return {"schema_version": "0.1", "commands": list(commands)}


class TestCommandFile:
    def test_minimal_valid_file(self) -> None:
        cf = CommandFile.model_validate(make_file({"op": "new_part"}))
        assert cf.schema_version == "0.1"
        assert cf.commands[0].op == "new_part"

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
            CommandFile.model_validate({"schema_version": "0.1", "commands": []})

    def test_extra_top_level_key_rejected(self) -> None:
        data = make_file({"op": "new_part"})
        data["units"] = "inches"
        with pytest.raises(ValidationError):
            CommandFile.model_validate(data)

    def test_extra_command_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandFile.model_validate(make_file({"op": "new_part", "color": "red"}))


class TestDimensions:
    @pytest.mark.parametrize("bad", [0, -5, "nan", "inf"])
    def test_rectangle_dimensions_must_be_positive_finite(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            DrawRectangle.model_validate({"op": "draw_rectangle", "width": bad, "height": 10})

    @pytest.mark.parametrize("bad", [0, -1, "nan"])
    def test_circle_diameter_must_be_positive_finite(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            DrawCircle.model_validate({"op": "draw_circle", "diameter": bad})

    def test_nan_center_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrawCircle.model_validate(
                {"op": "draw_circle", "center": ["nan", 0], "diameter": 5}
            )

    def test_center_defaults_to_origin(self) -> None:
        c = DrawCircle.model_validate({"op": "draw_circle", "diameter": 5})
        assert c.center == (0.0, 0.0)


class TestCutExtrude:
    def test_default_is_through_all(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude"})
        assert c.through_all is True
        assert c.depth is None

    def test_depth_alone_implies_blind(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude", "depth": 5})
        assert c.through_all is False
        assert c.depth == 5

    def test_through_all_with_depth_rejected(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            CutExtrude.model_validate({"op": "cut_extrude", "through_all": True, "depth": 5})

    def test_blind_without_depth_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires 'depth'"):
            CutExtrude.model_validate({"op": "cut_extrude", "through_all": False})


class TestSavePart:
    def test_valid_extension(self) -> None:
        assert SavePart.model_validate({"op": "save_part", "path": "a.SLDPRT"}).path == "a.SLDPRT"
        assert SavePart.model_validate({"op": "save_part", "path": "b.sldprt"}).path == "b.sldprt"

    def test_wrong_extension_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SLDPRT"):
            SavePart.model_validate({"op": "save_part", "path": "a.step"})

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SavePart.model_validate({"op": "save_part", "path": ""})
