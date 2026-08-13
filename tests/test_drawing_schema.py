# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Schema tests for the v0.4 drawing commands."""

import pytest
from pydantic import ValidationError

from swpilot.commands.schema import (
    SCHEMA_VERSION,
    CommandFile,
    CreateDrawing,
    IsometricView,
    SaveDrawing,
    SectionView,
    StandardViews,
)


class TestVersioning:
    def test_schema_version_is_05(self) -> None:
        assert SCHEMA_VERSION == "0.5"

    @pytest.mark.parametrize("version", ["0.1", "0.2", "0.3", "0.4"])
    def test_all_prior_versions_accepted(self, version: str) -> None:
        cf = CommandFile.model_validate(
            {"schema_version": version, "commands": [{"op": "new_part"}]}
        )
        assert cf.schema_version == version


class TestCreateDrawing:
    def test_defaults(self) -> None:
        c = CreateDrawing()
        assert c.sheet == "A3"
        assert c.scale is None
        assert c.projection == "third"
        assert c.drawn_by == "SW-Pilot"

    def test_scale_ratio(self) -> None:
        c = CreateDrawing.model_validate({"op": "create_drawing", "scale": [1, 2]})
        assert c.scale == (1, 2)

    def test_scale_rejects_zero_and_bool(self) -> None:
        with pytest.raises(ValidationError):
            CreateDrawing.model_validate({"op": "create_drawing", "scale": [0, 2]})
        with pytest.raises(ValidationError):
            CreateDrawing.model_validate({"op": "create_drawing", "scale": [True, 2]})

    def test_sheet_literal(self) -> None:
        with pytest.raises(ValidationError):
            CreateDrawing.model_validate({"op": "create_drawing", "sheet": "A2"})

    def test_round_trip(self) -> None:
        c = CreateDrawing.model_validate(
            {"op": "create_drawing", "of": "p", "scale": [1, 5], "projection": "first"}
        )
        assert CreateDrawing.model_validate(c.model_dump()) == c


class TestStandardViews:
    def test_default_all_three(self) -> None:
        assert StandardViews().views == ["front", "top", "right"]

    def test_front_required(self) -> None:
        with pytest.raises(ValidationError, match="'front' is required"):
            StandardViews.model_validate({"op": "standard_views", "views": ["top"]})

    def test_duplicates_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            StandardViews.model_validate(
                {"op": "standard_views", "views": ["front", "front"]}
            )

    def test_front_only_ok(self) -> None:
        assert StandardViews.model_validate(
            {"op": "standard_views", "views": ["front"]}
        ).views == ["front"]


class TestSectionAndIso:
    def test_section_defaults(self) -> None:
        s = SectionView()
        assert s.parent == "front"
        assert s.orientation == "vertical"

    def test_iso_defaults(self) -> None:
        i = IsometricView()
        assert i.corner == "top_right"
        assert i.scale is None


class TestSaveDrawing:
    def test_extension_enforced(self) -> None:
        with pytest.raises(ValidationError, match="SLDDRW"):
            SaveDrawing.model_validate({"op": "save_drawing", "path": "x.slddrt"})
        assert SaveDrawing.model_validate(
            {"op": "save_drawing", "path": "x.SLDDRW"}
        ).path.endswith("SLDDRW")


class TestFileLevel:
    def test_drawing_ops_parse_in_command_file(self) -> None:
        cf = CommandFile.model_validate(
            {
                "schema_version": "0.4",
                "commands": [
                    {"op": "new_part"},
                    {"op": "create_drawing", "sheet": "A4"},
                    {"op": "standard_views", "views": ["front", "right"]},
                    {"op": "isometric_view", "corner": "bottom_left"},
                    {"op": "section_view", "orientation": "horizontal"},
                    {"op": "smart_dimensions"},
                    {"op": "save_drawing", "path": "d.SLDDRW"},
                ],
            }
        )
        assert [c.op for c in cf.commands][1:] == [
            "create_drawing",
            "standard_views",
            "isometric_view",
            "section_view",
            "smart_dimensions",
            "save_drawing",
        ]
