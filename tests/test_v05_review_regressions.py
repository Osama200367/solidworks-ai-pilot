# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning fixes from the v0.5 adversarial review.

12 findings confirmed (0 refuted); one (the involute flank SIGN error) was
uncovered while fixing finding 3 and is pinned here too. COM-only fixes
(CreateSpline VARIANT, FeatureRevolve2 arity, InsertHelix interface) are
covered by call-plan assertions plus the WINDOWS_SETUP v0.5 checklist.
"""

import math

import pytest

from swpilot.backends import calls
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import expand_commands, load_and_expand, parse_command_data
from swpilot.executor import execute
from swpilot.model import curves as cv
from swpilot.model.drawing import DrawingTracker
from swpilot.model.tracker import ModelError, ModelTracker
from tests.test_curve_twin import build_gear
from tests.test_e2e_gears import EXAMPLES


class TestInvoluteSignAndFillet:
    """Finding 3 + the sign error it exposed: the tooth must NARROW toward
    the tip, and the root fillet must never rise above the base circle."""

    def test_tooth_narrows_toward_tip(self) -> None:
        # half-angle at the tip < at the pitch < at the base (an involute
        # tooth narrows). A + sign on φ inverts this and self-intersects.
        for z in (12, 20, 40, 60):
            tp = cv.spur_gear_tooth(2, z, 20)
            flank = next(s for s in tp.segments if isinstance(s, cv.SplineSeg))
            base = abs(math.atan2(flank.points[0][1], flank.points[0][0]))
            tip = abs(math.atan2(flank.points[-1][1], flank.points[-1][0]))
            assert tip < base, f"z={z} tooth does not narrow"
            # pitch point still exactly at π/(2z)
            rp = z  # module 2 → rp = z
            near = min(flank.points, key=lambda p: abs(math.hypot(*p) - rp))
            assert abs(math.atan2(near[1], near[0])) == pytest.approx(
                math.pi / (2 * z), abs=0.02
            )

    def test_shipped_wheel_z40_fillet_below_base(self) -> None:
        # gear_mesh_check.json's wheel is z40 — the finding-3 case. The
        # fillet must not push its tangent point above the base circle.
        tp = cv.spur_gear_tooth(2, 40, 20)
        # every profile point stays within [root, tip]; tip radius = m(z+2)/2
        rmax = max(math.hypot(*p) for s in tp.segments
                   for p in (s.points if isinstance(s, cv.SplineSeg) else s.endpoints()))
        assert rmax == pytest.approx(42.0, abs=1e-6)  # tip, not ballooned
        assert tp.invariants.fillet_radius <= 0.38 * 2 + 1e-9
        # z40 is a shallow sub-base tooth → sharp radial root, warned
        assert any("shallow sub-base" in w or "sharp radial root" in w
                   for w in tp.warnings)

    def test_z20_keeps_real_fillet(self) -> None:
        tp = cv.spur_gear_tooth(2, 20, 20)
        assert tp.invariants.fillet_radius == pytest.approx(0.76, abs=1e-6)


class TestSprocketFlankBounded:
    """The sprocket flank must stop at the tip, not balloon outward."""

    def test_flank_stays_within_tip(self) -> None:
        for chain, z in (("08B", 17), ("10B", 25), ("05B", 13), ("16B", 38)):
            sp = cv.sprocket_tooth(chain, z)
            rmax = max(math.hypot(*p) for s in sp.segments
                       for p in cv.sample_segment(s, 10))
            assert rmax <= sp.invariants.tip_dia / 2 + 0.6, f"{chain} z{z} balloons"


class TestRingGearInnerTip:
    """Finding 10: the inner tip land must reach the declared tip Ø."""

    def test_sub_base_tip_reaches_ra(self) -> None:
        # z20 m2: ra_i = 18 < rb = 18.79 → sub-base inner transition
        rg = cv.ring_gear_tooth_space(2, 20, 52)
        rmin = min(math.hypot(*p) for s in rg.segments
                   for p in cv.sample_segment(s, 8))
        assert rmin == pytest.approx(18.0, abs=1e-6)  # true tip m(z-2)/2
        assert any("tip radius lies below the base" in w for w in rg.warnings)


class TestCreateSplineMarshaling:
    """Finding 1 (critical): CreateSpline carries a flat float tuple that
    the COM backend must wrap as VT_ARRAY|VT_R8 (asserted structurally)."""

    def test_spec_is_flat_float_tuple(self) -> None:
        specs = calls.draw_spline_calls([(1.0, 2.0), (3.0, 4.0)])
        (spec,) = specs
        assert spec.method == "CreateSpline"
        assert spec.args[0] == (0.001, 0.002, 0.0, 0.003, 0.004, 0.0)
        assert all(isinstance(v, float) for v in spec.args[0])


class TestRevolveArity:
    """Findings 2/4 (critical): FeatureRevolve2 takes 20 positional args."""

    def test_twenty_args_with_offset_distances(self) -> None:
        specs = calls.revolve_calls("SWPilot_Axis_X", 360.0, False, "Revolve1")
        rev = next(c for c in specs if c.method == "FeatureRevolve2")
        assert len(rev.args) == 20
        # OffsetDistance1/2 (0.0, 0.0) at positions 12-13, ThinType at 14
        assert rev.args[12] == 0.0 and rev.args[13] == 0.0
        assert rev.args[14] == 0  # ThinType
        assert rev.args[8] == pytest.approx(2 * math.pi)  # Dir1Angle radians


class TestHelixCallPlan:
    """Findings 5/9: helix opens+closes a sketch and targets IModelDoc2."""

    def test_sketch_opened_and_helix_on_model(self) -> None:
        specs = calls.helix_thread_calls(16, 2, 40, True, 20.0, "Helix1")
        methods = [s.method for s in specs]
        # select plane → open sketch → circle → close sketch → InsertHelix
        assert methods[:5] == [
            "SelectByID2", "InsertSketch", "CreateCircleByRadius",
            "InsertSketch", "InsertHelix",
        ]
        helix = next(s for s in specs if s.method == "InsertHelix")
        assert helix.target == "Model"  # IModelDoc2, not FeatureManager
        assert helix.args[2] is False  # Clockwise = not right_handed


class TestGearDrawingEnvelope:
    """Finding 6: a drawing of a gear must bound the full tip disk."""

    def test_drawing_bounds_patterned_teeth(self) -> None:
        gear = build_gear(2, 20, 20, 20)
        gear.save_part("g.SLDPRT")
        d = DrawingTracker(
            name="d", model_doc="g", model=gear, model_path="g.SLDPRT",
            sheet="A3", scale=(1, 1), projection="third",
            title=None, drawn_by="x", date="",
        )
        lo, hi = d.aabb
        assert hi[0] - lo[0] == pytest.approx(44.0)  # tip diameter, not one tooth
        assert hi[1] - lo[1] == pytest.approx(44.0)


class TestRevolveOffsetPlane:
    """Finding 7: an offset sketch plane must fold into the revolve radius
    and must not spuriously trip the crossing-axis guard."""

    def test_offset_plane_radius_includes_offset(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_axis("x")
        t.create_plane("up", "front", 20)  # sketch 20 mm off the x-axis
        t.create_sketch("up")
        for a, b in (((-5, 3), (5, 3)), ((5, 3), (5, 8)), ((5, 8), (-5, 8)),
                     ((-5, 8), (-5, 3))):
            t.draw_line(a, b)
        f = t.revolve("x", 360.0, False)
        # front-plane offset 20 is along z (perpendicular to the x-axis);
        # v ranges 3..8, so the true max radius folds in the offset:
        # √(8² + 20²) — proving the offset is not dropped.
        assert f.curve_radius == pytest.approx(math.hypot(8.0, 20.0), abs=1e-6)

    def test_offset_plane_does_not_falsely_reject(self) -> None:
        # a profile straddling v=0 on an OFFSET plane does not cross the
        # world axis (the axis is 20 mm away), so it must be accepted
        t = ModelTracker()
        t.new_part()
        t.create_axis("x")
        t.create_plane("up", "front", 20)
        t.create_sketch("up")
        for a, b in (((-5, -3), (5, -3)), ((5, -3), (5, 3)), ((5, 3), (-5, 3)),
                     ((-5, 3), (-5, -3))):
            t.draw_line(a, b)
        f = t.revolve("x", 360.0, False)  # must not raise
        assert f.curved


class TestCurvedCutPatternAabb:
    """Finding 11: a curved circular pattern of a CUT seed must degrade
    gracefully, not assert on an unset curve_bbox."""

    def test_ring_gear_pattern_not_curved_boss(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "internal_ring_gear", "module": 2, "teeth": 40,
                     "face_width": 15, "rim_outer_diameter": 95},
                    {"op": "save_part", "path": "ring.SLDPRT"},
                ],
            }
        )
        expanded = expand_commands(list(cf.commands))
        b = MockBackend()
        report = execute(expanded, b)
        assert report.success
        # the pattern feature is a cut pattern → not curve_full_disk; asking
        # for its aabb degrades to a clear ModelError, never an assertion
        from swpilot.model.apply import apply_to_session
        from swpilot.model.session import SessionTracker
        s = SessionTracker()
        for ec in expanded:
            apply_to_session(s, ec.command)
        part = s.documents["Part1"]
        assert isinstance(part, ModelTracker)
        pat = next(f for f in part.features if f.kind == "circular_pattern")
        assert not pat.curved
        with pytest.raises(ModelError, match="no boundable geometry"):
            part.feature_aabb(pat.name)


class TestKeywayDepth:
    """Finding 8: the keyway outer edge sits exactly `depth` past the wall."""

    def test_outer_edge_at_r_plus_depth(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "involute_spur_gear", "name": "g", "module": 2,
                     "teeth": 20, "face_width": 10, "bore": 16,
                     "keyway": {"width": 5, "depth": 2.3}},
                    {"op": "save_part", "path": "g.SLDPRT"},
                ],
            }
        )
        b = MockBackend()
        execute(expand_commands(list(cf.commands)), b)
        rect = next(c for c in b.call_log if c.method == "CreateCenterRectangle")
        cy, corner_y = rect.args[1], rect.args[4]
        outer_edge_mm = (cy + (corner_y - cy)) * 1000.0
        assert outer_edge_mm == pytest.approx(10.3)  # r(8) + depth(2.3)


class TestHelixOnFeature:
    """Finding 12: on_feature is no longer a silent no-op."""

    def test_on_feature_validated_and_warned(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 0), 16)
        t.extrude(40, False)
        t.pop_warnings()
        t.helix_thread(16, 2, 30, True, on_feature="Boss-Extrude1")
        assert any("on_feature" in w for w in t.pop_warnings())

    def test_unknown_on_feature_rejected(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 0), 16)
        t.extrude(40, False)
        with pytest.raises(ModelError, match="unknown feature"):
            t.helix_thread(16, 2, 30, True, on_feature="Nope")


class TestExamplesStillBuild:
    def test_all_three_examples_succeed(self) -> None:
        for name in ("spur_gear_m2_z20.json", "gear_mesh_check.json",
                     "v_pulley_revolved.json"):
            _, expanded = load_and_expand(EXAMPLES / name)
            report = execute(expanded, MockBackend())
            assert report.success, name
