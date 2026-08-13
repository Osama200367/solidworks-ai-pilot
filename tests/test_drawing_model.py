# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Drawing twin tests: layout, placement validation, sheet-space mapping."""

import pytest

from swpilot.model.drawing import (
    DIM_BAND,
    NOTES_BAND,
    SHEET_MARGIN,
    TITLE_BLOCK_HEIGHT,
    DrawingTracker,
)
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import ModelError, ModelTracker


def make_plate(w: float = 120.0, h: float = 80.0, t: float = 12.0) -> ModelTracker:
    part = ModelTracker()
    part.new_part()
    part.create_sketch("front")
    part.draw_rectangle((0, 0), w, h)
    part.extrude(t, False)
    part.save_part("plate.SLDPRT")
    part.pop_warnings()
    return part


def make_drawing(
    part: ModelTracker | None = None,
    sheet: str = "A3",
    scale: tuple[int, int] | None = (1, 1),
    projection: str = "third",
) -> DrawingTracker:
    part = part or make_plate()
    return DrawingTracker(
        name="d",
        model_doc="p",
        model=part,
        model_path="plate.SLDPRT",
        sheet=sheet,
        scale=scale,
        projection=projection,
        title=None,
        drawn_by="SW-Pilot",
        date="",
    )


class TestSessionRouting:
    def test_create_drawing_requires_saved_model(self) -> None:
        session = SessionTracker()
        session.new_part("p")
        part = session.documents["p"]
        assert isinstance(part, ModelTracker)
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 50, 50)
        part.extrude(10, False)
        with pytest.raises(ModelError, match="has not been saved"):
            session.new_drawing(None, "p", "A4", None, "third", None, "x", "")

    def test_drawing_of_drawing_rejected(self) -> None:
        session = SessionTracker()
        session.new_part("p")
        part = session.documents["p"]
        assert isinstance(part, ModelTracker)
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 50, 50)
        part.extrude(10, False)
        part.save_part("p.SLDPRT")
        session.new_drawing("d1", "p", "A4", (1, 2), "third", None, "x", "")
        with pytest.raises(ModelError, match="is itself a drawing"):
            session.new_drawing("d2", "d1", "A4", None, "third", None, "x", "")

    def test_part_command_on_drawing_rejected(self) -> None:
        session = SessionTracker()
        session.new_part("p")
        part = session.documents["p"]
        assert isinstance(part, ModelTracker)
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 50, 50)
        part.extrude(10, False)
        part.save_part("p.SLDPRT")
        session.new_drawing("d1", "p", "A4", (1, 2), "third", None, "x", "")
        with pytest.raises(ModelError, match="is a drawing"):
            session.active_part("create_sketch")

    def test_no_geometry_rejected(self) -> None:
        session = SessionTracker()
        session.new_part("p")
        part = session.documents["p"]
        assert isinstance(part, ModelTracker)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        with pytest.raises(ModelError, match="no solid geometry"):
            session.new_drawing(None, "p", "A4", None, "third", None, "x", "")


class TestThirdAngleLayout:
    def test_standard_positions(self) -> None:
        d = make_drawing()
        specs = d.standard_views(["front", "top", "right"])
        assert [s.name for s in specs] == ["front", "top", "right"]
        front, top, right = (d.views[n] for n in ("front", "top", "right"))
        # third angle: top ABOVE front, right RIGHT of front
        assert top.center[0] == front.center[0]
        assert top.center[1] > front.center[1]
        assert right.center[1] == front.center[1]
        assert right.center[0] > front.center[0]
        # front view is 120x80 at 1:1
        assert front.size == (120.0, 80.0)
        assert top.size == (120.0, 12.0)
        assert right.size == (12.0, 80.0)

    def test_front_is_model_view_others_projected(self) -> None:
        d = make_drawing()
        specs = d.standard_views(["front", "top", "right"])
        assert specs[0].method == "model" and specs[0].orientation == "*Front"
        assert specs[0].model_path == "plate.SLDPRT"
        assert all(s.method == "projected" and s.parent == "front" for s in specs[1:])

    def test_group_anchored_in_content_area(self) -> None:
        d = make_drawing()
        d.standard_views(["front", "top", "right"])
        front = d.views["front"]
        x0 = front.center[0] - front.size[0] / 2.0 - DIM_BAND
        y0 = front.center[1] - front.size[1] / 2.0 - DIM_BAND - NOTES_BAND
        assert x0 == pytest.approx(SHEET_MARGIN)
        assert y0 == pytest.approx(SHEET_MARGIN + TITLE_BLOCK_HEIGHT)


class TestFirstAngleLayout:
    def test_positions_flip(self) -> None:
        d = make_drawing(projection="first")
        d.standard_views(["front", "top", "right"])
        front, top, right = (d.views[n] for n in ("front", "top", "right"))
        assert top.center[1] < front.center[1]  # top BELOW front
        assert right.center[0] < front.center[0]  # right LEFT of front

    def test_view_images_are_projection_invariant(self) -> None:
        # First vs third angle changes only view PLACEMENT; the image of
        # an individual view is identical under both conventions (v0.4
        # review finding: flipping z here mirrored every first-angle pick).
        d3 = make_drawing()
        d1 = make_drawing(projection="first")
        for d in (d3, d1):
            d.standard_views(["front", "top", "right"])
        p = (10.0, 20.0, 5.0)
        for view in ("front", "top", "right"):
            assert d3.project(d3.views[view], p) == d1.project(d1.views[view], p)
        assert d3.project(d3.views["top"], p) == (10.0, -5.0)
        assert d3.project(d3.views["right"], p) == (-5.0, 20.0)


class TestFitValidation:
    def test_oversized_scale_rejected_with_suggestion(self) -> None:
        d = make_drawing(sheet="A4", scale=(2, 1))
        with pytest.raises(ModelError, match="smaller scale or a larger sheet"):
            d.standard_views(["front", "top", "right"])

    def test_auto_scale_picks_largest_fitting(self) -> None:
        d = make_drawing(scale=None)  # A3, 120x80x12 plate
        assert d.scale_ratio == (1, 1)
        d_small = make_drawing(sheet="A4", scale=None)
        assert d_small.scale_ratio[0] / d_small.scale_ratio[1] < 1.0

    def test_iso_overlap_rejected(self) -> None:
        d = make_drawing()
        d.standard_views(["front", "top", "right"])
        with pytest.raises(ModelError, match="overlaps"):
            # bottom_left corner collides with the front view cell
            d.isometric_view("bottom_left", (1, 1))

    def test_iso_fits_in_free_corner(self) -> None:
        d = make_drawing()
        d.standard_views(["front", "top", "right"])
        spec = d.isometric_view("top_right", None)
        assert spec.orientation == "*Isometric"
        # default iso scale: one series step below the sheet scale
        assert spec.scale == pytest.approx(0.5)

    def test_duplicate_views_rejected(self) -> None:
        d = make_drawing()
        d.standard_views(["front"])
        with pytest.raises(ModelError, match="already exists"):
            d.standard_views(["front"])


class TestSectionView:
    def test_vertical_section_third_angle(self) -> None:
        d = make_drawing()
        d.standard_views(["front", "top", "right"])
        spec = d.section_view("front", "vertical")
        assert spec.label == "A"
        assert spec.name == "section_A"
        front = d.views["front"]
        # The cutting line is in PARENT-VIEW sketch coordinates (model
        # scale, origin = the projection of the model origin): a vertical
        # line through the plate's center, taller than the 80mm plate.
        x1, y1, x2, y2 = spec.line
        ox, oy = d.sheet_point("front", (0.0, 0.0, 0.0))
        assert x1 == x2 == pytest.approx((front.center[0] - ox) / front.scale)
        assert y2 - y1 == pytest.approx((front.size[1] + 8.0) / front.scale)
        assert y1 == pytest.approx(-y2)  # centered on the model
        # placed to the right of everything in third angle
        assert spec.position[0] > d.views["right"].center[0]
        assert spec.position[1] == front.center[1]

    def test_vertical_section_first_angle_also_goes_right(self) -> None:
        # Sections always extend past the right edge; the IMAGE flips with
        # the projection angle (SolidWorks orients the arrows), not the side.
        d = make_drawing(sheet="A3", scale=(1, 2), projection="first")
        d.standard_views(["front", "top", "right"])
        spec = d.section_view("front", "vertical")
        assert spec.position[0] > max(v.center[0] for v in d.views.values() if v.kind != "section")
        sec = d.views["section_A"]
        assert d.project(sec, (10.0, 20.0, 5.0)) == (5.0, 20.0)  # first angle: +z

    def test_section_labels_advance(self) -> None:
        d = make_drawing(sheet="A3", scale=(1, 2))
        d.standard_views(["front"])
        assert d.section_view("front", "vertical").label == "A"
        b = d.section_view("front", "horizontal")
        assert b.label == "B"
        # horizontal sections go above everything already placed
        assert b.position[1] > d.views["front"].center[1]

    def test_section_requires_existing_parent(self) -> None:
        d = make_drawing()
        with pytest.raises(ModelError, match="does not exist yet"):
            d.section_view("front", "vertical")

    def test_section_of_non_front_parent_rejected(self) -> None:
        d = make_drawing(sheet="A3", scale=(1, 2))
        d.standard_views(["front", "top", "right"])
        with pytest.raises(ModelError, match="front view only"):
            d.section_view("top", "vertical")

    def test_section_projection_mapping(self) -> None:
        d = make_drawing(sheet="A3", scale=(1, 2))
        d.standard_views(["front"])
        d.section_view("front", "vertical")
        sec = d.views["section_A"]
        # vertical section image = right-view image in third angle
        assert d.project(sec, (10.0, 20.0, 5.0)) == (-5.0, 20.0)


class TestSheetPoint:
    def test_front_view_mapping(self) -> None:
        d = make_drawing()
        d.standard_views(["front"])
        front = d.views["front"]
        # model center maps to the view center; +x maps right by scale
        cx, cy, cz = d.model_center
        assert d.sheet_point("front", (cx, cy, cz)) == pytest.approx(front.center)
        px, py = d.sheet_point("front", (cx + 10.0, cy, cz))
        assert px == pytest.approx(front.center[0] + 10.0)
        assert py == pytest.approx(front.center[1])

    def test_scale_applied(self) -> None:
        d = make_drawing(sheet="A3", scale=(1, 2))
        d.standard_views(["front"])
        cx, cy, cz = d.model_center
        px, _ = d.sheet_point("front", (cx + 10.0, cy, cz))
        assert px == pytest.approx(d.views["front"].center[0] + 5.0)


class TestStaleModelWarning:
    def test_model_changed_after_save_warns(self) -> None:
        part = make_plate()
        part.create_sketch("front")
        part.draw_circle((0, 0), 10)
        part.cut_extrude(True, None, False, None)  # feature AFTER the save
        part.pop_warnings()
        d = make_drawing(part)
        assert any("changed after its last save" in w for w in d.pop_warnings())
        d.standard_views(["front"])
        assert any("changed after its last save" in w for w in d.pop_warnings())


class TestSaveAndSummary:
    def test_summary_shape(self) -> None:
        d = make_drawing()
        d.standard_views(["front", "top", "right"])
        d.save_drawing("plate.SLDDRW")
        s = d.summary()
        assert s["kind"] == "drawing"
        assert s["of"] == "p"
        assert s["scale"] == "1:1"
        assert [v["name"] for v in s["views"]] == ["front", "top", "right"]  # type: ignore[index]
        assert s["saved_to"] == ["plate.SLDDRW"]

    def test_save_without_views_warns(self) -> None:
        d = make_drawing()
        d.save_drawing("plate.SLDDRW")
        assert any("no views" in w for w in d.pop_warnings())
