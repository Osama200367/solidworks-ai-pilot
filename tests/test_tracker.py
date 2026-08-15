# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""ModelTracker tests: lifecycle, validation, 3D derivation, selectors.

Ports every v0.1 mock-simulator scenario (the rules moved here) and adds
the v0.2 layer: planes/axes, edge derivation with world pick points,
selector resolution, fillet/chamfer limits, and pattern validation.
"""

import pytest

from swpilot.model.tracker import AXIS_FEATURE_NAMES, ModelError, ModelTracker


@pytest.fixture
def tr() -> ModelTracker:
    return ModelTracker()


def make_plate(tr: ModelTracker, w: float = 100, h: float = 50, t: float = 10) -> None:
    tr.new_part()
    tr.create_sketch("front")
    tr.draw_rectangle((0, 0), w, h)
    tr.extrude(t, reverse=False)


def cut_circle(tr: ModelTracker, center: tuple[float, float], d: float) -> None:
    tr.create_sketch("front")
    tr.draw_circle(center, d)
    tr.cut_extrude(True, None, False, None)


class TestLifecycleRules:
    def test_sketch_without_part(self, tr: ModelTracker) -> None:
        with pytest.raises(ModelError, match="no part is open"):
            tr.create_sketch("front")

    def test_draw_without_sketch(self, tr: ModelTracker) -> None:
        tr.new_part()
        with pytest.raises(ModelError, match="no active sketch"):
            tr.draw_circle((0, 0), 5)

    def test_second_new_part_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        with pytest.raises(ModelError, match="already open"):
            tr.new_part()

    def test_extrude_empty_sketch_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        with pytest.raises(ModelError, match="empty"):
            tr.extrude(10, False)

    def test_second_sketch_while_active_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        with pytest.raises(ModelError, match="still active"):
            tr.create_sketch("top")

    def test_cut_without_solid_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 5)
        with pytest.raises(ModelError, match="no solid material"):
            tr.cut_extrude(True, None, False, None)

    def test_unknown_plane_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        with pytest.raises(ModelError, match="unknown plane"):
            tr.create_sketch("fornt")


class TestContourValidation:
    def test_overlapping_circles_in_one_sketch_rejected(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 10)
        with pytest.raises(ModelError, match="overlaps or touches"):
            tr.draw_circle((5, 0), 10)

    def test_nested_contours_allowed(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_rectangle((0, 0), 100, 50)
        tr.draw_circle((0, 0), 10)  # a hole contour inside the outline: fine
        tr.extrude(10, False)

    def test_degenerate_slot_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        with pytest.raises(ModelError, match="coincide"):
            tr.draw_slot((5, 5), (5, 5), 4)


class TestCutContainment:
    def test_hole_inside_plate_ok(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (40, 15), 8)
        assert [f.name for f in tr.features] == ["Boss-Extrude1", "Cut-Extrude1"]

    def test_hole_outside_plate_rejected(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((60, 0), 8)
        with pytest.raises(ModelError, match="miss the part entirely"):
            tr.cut_extrude(True, None, False, None)

    def test_hole_crossing_plate_edge_warns(self, tr: ModelTracker) -> None:
        # v1.2: edge-crossing cuts (flats, channels, rim windows) are standard
        # SolidWorks operations — the twin warns instead of rejecting, and
        # delegates exact-tangency rejection to Windows verification.
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((50, 0), 8)
        tr.cut_extrude(True, None, False, None)
        assert any("crosses or touches a material edge" in w for w in tr.pop_warnings())

    def test_hole_tangent_to_edge_warns(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((46, 0), 8)  # tangent to x=50 edge: zero-thickness
        tr.cut_extrude(True, None, False, None)
        assert any("SolidWorks will reject" in w for w in tr.pop_warnings())

    def test_seam_spanning_cut_warns_instead_of_failing(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 5)
        tr.create_sketch("front")
        tr.draw_rectangle((70, 0), 60, 50)  # x in [40, 100]: overlaps the first
        tr.extrude(5, False)
        tr.pop_warnings()
        tr.create_sketch("front")
        tr.draw_circle((45, 0), 12)  # inside the union, inside neither rect
        tr.cut_extrude(True, None, False, None)
        assert any("footprint unions" in w for w in tr.pop_warnings())

    def test_cut_inside_removed_material_rejected(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (0, 0), 20)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 10)  # entirely inside the removed d=20 disk
        with pytest.raises(ModelError, match="already removed by Cut-Extrude1"):
            tr.cut_extrude(True, None, False, None)

    def test_duplicate_hole_rejected(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (0, 0), 20)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 20)  # identical to the previous hole
        with pytest.raises(ModelError, match="already removed"):
            tr.cut_extrude(True, None, False, None)

    def test_cut_on_parallel_offset_plane_still_validated(self, tr: ModelTracker) -> None:
        # v0.2 upgrade over v0.1: parallel planes share a family, so a cut
        # sketched on the top face is checked against the base footprint.
        make_plate(tr)
        tr.create_plane("Top1", "front", 10.0)
        tr.create_sketch("Top1")
        tr.draw_circle((60, 0), 8)
        with pytest.raises(ModelError, match="miss the part entirely"):
            tr.cut_extrude(True, None, True, None)

    def test_cross_family_cut_warns(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.create_sketch("top")
        tr.draw_circle((0, 0), 8)
        tr.cut_extrude(True, None, False, None)
        assert any("cross-family" in w for w in tr.pop_warnings())


class TestPlanesAndAxes:
    def test_create_plane_and_reuse_lookup(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_plane("P1", "front", 12.0)
        assert tr.find_plane_at("front", 12.0) == "P1"
        assert tr.plane_display_name("P1") == "P1"
        assert tr.plane_display_name("front") == "Front Plane"

    def test_duplicate_plane_name_rejected(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_plane("P1", "front", 12.0)
        with pytest.raises(ModelError, match="already exists"):
            tr.create_plane("P1", "front", 20.0)

    def test_coincident_plane_warns(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_plane("P1", "front", 12.0)
        tr.pop_warnings()
        tr.create_plane("P2", "front", 12.0)
        assert any("coincides" in w for w in tr.pop_warnings())

    def test_stacked_offset_plane(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_plane("P1", "front", 10.0)
        tr.create_plane("P2", "P1", 5.0)
        assert tr.frame("P2").offset == 15.0

    def test_create_axis_and_duplicate(self, tr: ModelTracker) -> None:
        tr.new_part()
        assert tr.create_axis("z") == AXIS_FEATURE_NAMES["z"]
        with pytest.raises(ModelError, match="already exists"):
            tr.create_axis("z")


class TestEdgeDerivation:
    def test_rect_boss_edge_counts(self, tr: ModelTracker) -> None:
        make_plate(tr, 120, 80, 12)
        boss = tr.features[0]
        groups = [e.group for e in boss.edges]
        assert groups.count("vertical_corners") == 4
        assert groups.count("top_loop") == 4
        assert groups.count("bottom_loop") == 4

    def test_rect_boss_corner_midpoints_world(self, tr: ModelTracker) -> None:
        make_plate(tr, 120, 80, 12)
        corners = {
            e.midpoint for e in tr.features[0].edges if e.group == "vertical_corners"
        }
        assert corners == {
            (-60.0, -40.0, 6.0),
            (60.0, -40.0, 6.0),
            (60.0, 40.0, 6.0),
            (-60.0, 40.0, 6.0),
        }

    def test_top_loop_at_full_depth(self, tr: ModelTracker) -> None:
        make_plate(tr, 120, 80, 12)
        tops = [e for e in tr.features[0].edges if e.group == "top_loop"]
        assert all(e.midpoint[2] == 12.0 for e in tops)
        bottoms = [e for e in tr.features[0].edges if e.group == "bottom_loop"]
        assert all(e.midpoint[2] == 0.0 for e in bottoms)

    def test_top_plane_boss_maps_to_world(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("top")
        tr.draw_rectangle((0, 0), 40, 20)
        tr.extrude(6, False)
        corners = {
            e.midpoint for e in tr.features[0].edges if e.group == "vertical_corners"
        }
        # top-plane sketch (u, v) -> world (u, 0, -v); extrusion along +y
        assert corners == {
            (-20.0, 3.0, 10.0),
            (20.0, 3.0, 10.0),
            (-20.0, 3.0, -10.0),
            (20.0, 3.0, -10.0),
        }

    def test_circle_boss_has_no_vertical_corners(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 30)
        tr.extrude(10, False)
        groups = {e.group for e in tr.features[0].edges}
        assert groups == {"top_loop", "bottom_loop"}

    def test_cut_rims_at_material_surfaces(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 10)
        cut_circle(tr, (20, 0), 8)
        cut = tr.features[1]
        rim_z = {e.group: e.midpoint[2] for e in cut.edges}
        assert rim_z["top_loop"] == 10.0
        assert rim_z["bottom_loop"] == 0.0

    def test_reversed_boss_extends_negative(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_rectangle((0, 0), 40, 20)
        tr.extrude(6, reverse=True)
        assert tr.material_interval("front") == (-6.0, 0.0)
        tops = [e for e in tr.features[0].edges if e.group == "top_loop"]
        assert all(e.midpoint[2] == -6.0 for e in tops)


class TestSelectors:
    def test_vertical_corners_default_feature(self, tr: ModelTracker) -> None:
        make_plate(tr)
        edges = tr.resolve_edges("fillet", "vertical_corners", None, None)
        assert len(edges) == 4
        assert all(e.feature == "Boss-Extrude1" for e in edges)

    def test_select_all(self, tr: ModelTracker) -> None:
        make_plate(tr)
        assert len(tr.resolve_edges("fillet", "all", None, None)) == 12

    def test_of_feature_targets_cut_rims(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (20, 0), 8)
        edges = tr.resolve_edges("chamfer", "top_loop", "Cut-Extrude1", None)
        assert len(edges) == 1
        assert edges[0].feature == "Cut-Extrude1"

    def test_missing_group_default_feature(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 30)
        tr.extrude(10, False)
        with pytest.raises(ModelError, match="vertical_corners"):
            tr.resolve_edges("fillet", "vertical_corners", None, None)

    def test_missing_group_explicit_feature_reports_available(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 30)
        tr.extrude(10, False)
        with pytest.raises(ModelError, match="available groups"):
            tr.resolve_edges("fillet", "vertical_corners", "Boss-Extrude1", None)

    def test_default_feature_skips_featureless_groups(self, tr: ModelTracker) -> None:
        # "fillet the corners" after drilling a hole targets the plate,
        # not the hole cut (which has no corner edges).
        make_plate(tr)
        cut_circle(tr, (20, 0), 8)
        edges = tr.resolve_edges("fillet", "vertical_corners", None, None)
        assert all(e.feature == "Boss-Extrude1" for e in edges)

    def test_unknown_feature(self, tr: ModelTracker) -> None:
        make_plate(tr)
        with pytest.raises(ModelError, match="unknown feature"):
            tr.resolve_edges("fillet", "all", "Nope1", None)

    def test_near_point_picks_nearest(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 10)
        edges = tr.resolve_edges("fillet", None, None, (50.0, 25.0, 5.0))
        assert len(edges) == 1
        assert edges[0].midpoint == (50.0, 25.0, 5.0)

    def test_near_point_far_away_warns(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 10)
        tr.resolve_edges("fillet", None, None, (200.0, 0.0, 0.0))
        assert any("check that this is the intended edge" in w for w in tr.pop_warnings())


class TestFilletChamfer:
    def test_fillet_consumes_edges(self, tr: ModelTracker) -> None:
        make_plate(tr)
        feature, edges = tr.fillet(5, "vertical_corners", None, None)
        assert feature.name == "Fillet1"
        assert all(e.consumed_by == "Fillet1" for e in edges)
        with pytest.raises(ModelError, match="no unconsumed"):
            tr.resolve_edges("fillet", "vertical_corners", "Boss-Extrude1", None)

    def test_fillet_radius_limit(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 10)
        with pytest.raises(ModelError, match="too large"):
            tr.fillet(25, "vertical_corners", None, None)  # half of 50 is the cap

    def test_chamfer_on_single_loop_allows_up_to_full_depth(self, tr: ModelTracker) -> None:
        # 5mm chamfer on the top edge of a 10mm plate is perfectly valid —
        # only selecting BOTH loops halves the depth budget.
        make_plate(tr, 100, 50, 10)
        tr.chamfer(5, 45, "top_loop", None, None)

    def test_chamfer_distance_limit_on_top_loop(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 50, 10)
        with pytest.raises(ModelError, match="too large"):
            tr.chamfer(12, 45, "top_loop", None, None)  # capped by depth (10)


class TestPatterns:
    def test_pattern_requires_axis(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (-30, 0), 8)
        with pytest.raises(ModelError, match="does not exist"):
            tr.linear_pattern(["Cut-Extrude1"], "x", 20, 3, None)

    def test_linear_pattern_inside_material_no_warning(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (-30, 0), 8)
        tr.create_axis("x")
        tr.pop_warnings()
        tr.linear_pattern(["Cut-Extrude1"], "x", 30, 3, None)
        assert tr.pop_warnings() == []

    def test_linear_pattern_escaping_material_warns(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (-30, 0), 8)
        tr.create_axis("x")
        tr.pop_warnings()
        tr.linear_pattern(["Cut-Extrude1"], "x", 50, 3, None)  # instance at x=70
        assert any("may reject that instance" in w for w in tr.pop_warnings())

    def test_pattern_instances_block_duplicate_cuts(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (-30, 0), 8)
        tr.create_axis("x")
        tr.linear_pattern(["Cut-Extrude1"], "x", 30, 3, None)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)  # instance 1 sits exactly here
        with pytest.raises(ModelError, match="already removed"):
            tr.cut_extrude(True, None, False, None)

    def test_circular_pattern_rotates_circles(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 100, 8)
        cut_circle(tr, (0, -30), 6)
        tr.create_axis("z")
        tr.pop_warnings()
        tr.circular_pattern(["Cut-Extrude1"], "z", 4, 360.0, True)
        assert tr.pop_warnings() == []

    def test_circular_pattern_of_rect_warns(self, tr: ModelTracker) -> None:
        make_plate(tr, 100, 100, 8)
        tr.create_sketch("front")
        tr.draw_rectangle((0, -30), 10, 5)
        tr.cut_extrude(True, None, False, None)
        tr.create_axis("z")
        tr.pop_warnings()
        tr.circular_pattern(["Cut-Extrude1"], "z", 4, 360.0, True)
        assert any("cannot be containment-checked" in w for w in tr.pop_warnings())

    def test_pattern_of_fillet_rejected(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.fillet(5, "vertical_corners", None, None)
        tr.create_axis("x")
        with pytest.raises(ModelError, match="only boss/cut"):
            tr.linear_pattern(["Fillet1"], "x", 20, 2, None)


class TestStateAndNaming:
    def test_feature_and_sketch_naming(self, tr: ModelTracker) -> None:
        make_plate(tr)
        cut_circle(tr, (40, 15), 8)
        summary = tr.summary()
        assert [s["name"] for s in summary["sketches"]] == ["Sketch1", "Sketch2"]
        assert [f["name"] for f in summary["features"]] == ["Boss-Extrude1", "Cut-Extrude1"]
        assert summary["sketches"][0]["consumed_by"] == "Boss-Extrude1"

    def test_save_and_warnings(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.save_part("empty.SLDPRT")
        warnings = tr.pop_warnings()
        assert any("no solid geometry" in w for w in warnings)
        assert any("relative" in w for w in warnings)
        assert tr.summary()["saved_to"] == ["empty.SLDPRT"]

    def test_absolute_save_path_no_relative_warning(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.save_part("/abs/out.SLDPRT")
        assert not any("relative" in w for w in tr.pop_warnings())

    def test_unconsumed_sketch_warns_at_finalize(self, tr: ModelTracker) -> None:
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 5)
        tr.finalize()
        assert any("unconsumed sketch" in w for w in tr.pop_warnings())

    def test_feature_aabb(self, tr: ModelTracker) -> None:
        make_plate(tr, 120, 80, 12)
        mins, maxs = tr.feature_aabb("Boss-Extrude1")
        assert mins == (-60.0, -40.0, 0.0)
        assert maxs == (60.0, 40.0, 12.0)
