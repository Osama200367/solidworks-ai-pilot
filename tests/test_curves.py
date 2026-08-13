# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Curve-math tests: the invariants the twin verifies in CI.

These pin the pure geometry — involute unwind, standard gear math,
ISO-606 sprocket formulas, revolve envelope, helix — independently of any
SolidWorks call. Curve *fidelity* (spline fit, solid validity) is
Windows-only and lives in the WINDOWS_SETUP checklist.
"""

import math

import pytest

from swpilot.model import curves as c


class TestInvoluteMath:
    def test_inv_function(self) -> None:
        a = math.radians(20.0)
        assert c.involute_inv(a) == pytest.approx(math.tan(a) - a)
        assert c.involute_inv(a) == pytest.approx(0.014904, abs=1e-6)

    def test_undercut_limit_20deg(self) -> None:
        # 2/sin²20° ≈ 17.1 → z=17 undercuts, z=18 does not
        assert c.undercut_limit(20.0) == pytest.approx(17.097, abs=1e-3)


class TestSpurGearInvariants:
    def test_standard_metric_math(self) -> None:
        tp = c.spur_gear_tooth(2, 20, 20)
        i = tp.invariants
        assert i.pitch_dia == 40.0  # m·z
        assert i.base_dia == pytest.approx(40.0 * math.cos(math.radians(20)))
        assert i.tip_dia == 44.0  # m(z+2)
        assert i.root_dia == 35.0  # m(z-2.5)
        assert i.tooth_thickness_pitch == pytest.approx(math.pi * 2 / 2)  # πm/2

    def test_fillet_is_038_module(self) -> None:
        tp = c.spur_gear_tooth(2, 20, 20)
        assert tp.invariants.fillet_radius == pytest.approx(0.38 * 2, abs=1e-6)

    def test_z20_no_undercut_but_sub_base(self) -> None:
        tp = c.spur_gear_tooth(2, 20, 20)
        assert tp.invariants.undercut is False
        assert tp.invariants.sub_base_flank is True  # root below base circle
        assert not tp.invariants.pointed_tip

    def test_low_tooth_count_undercuts(self) -> None:
        tp = c.spur_gear_tooth(2, 12, 20)
        assert tp.invariants.undercut is True
        assert any("undercut" in w for w in tp.warnings)

    def test_tooth_loop_is_closed(self) -> None:
        for z in (12, 17, 20, 40, 127):
            tp = c.spur_gear_tooth(2, z, 20)
            assert c.loop_is_closed(list(tp.segments)), f"z={z} not closed"

    def test_all_points_within_root_tip_annulus(self) -> None:
        tp = c.spur_gear_tooth(2, 20, 20)
        rmin, rmax = math.inf, 0.0
        for s in tp.segments:
            pts = s.points if isinstance(s, c.SplineSeg) else s.endpoints()
            for p in pts:
                r = math.hypot(*p)
                rmin, rmax = min(rmin, r), max(rmax, r)
        assert rmin == pytest.approx(17.5, abs=1e-6)  # root radius
        assert rmax == pytest.approx(22.0, abs=1e-6)  # tip radius

    def test_pitch_point_at_half_tooth_angle(self) -> None:
        # A flank point at the pitch circle must sit at ±π/(2z) from the
        # tooth centerline — the definition of standard tooth thickness.
        m, z, a = 2, 20, 20.0
        tp = c.spur_gear_tooth(m, z, a)
        left = [s for s in tp.segments if isinstance(s, c.SplineSeg)]
        # find the left-flank point nearest the pitch radius
        rp = m * z / 2
        best = min(
            (p for s in left for p in s.points),
            key=lambda p: abs(math.hypot(*p) - rp),
        )
        assert math.hypot(*best) == pytest.approx(rp, abs=0.3)
        assert abs(math.atan2(best[1], best[0])) == pytest.approx(
            math.pi / (2 * z), abs=0.02
        )

    def test_spline_flanks_present(self) -> None:
        tp = c.spur_gear_tooth(2, 20, 20)
        splines = [s for s in tp.segments if isinstance(s, c.SplineSeg)]
        assert len(splines) == 2  # two involute flanks
        assert all(len(s.points) == 18 for s in splines)


class TestGearMesh:
    def test_center_distance(self) -> None:
        assert c.gear_center_distance(2, 20, 40) == 60.0
        assert c.gear_center_distance(1.5, 18, 18) == 27.0


class TestRingGear:
    def test_internal_addendum_dedendum_invert(self) -> None:
        rg = c.ring_gear_tooth_space(2, 40, 90)
        i = rg.invariants
        assert i.pitch_dia == 80.0
        assert i.tip_dia == 76.0  # m(z-2): tip points INWARD
        assert i.root_dia == 85.0  # m(z+2.5): root is OUTWARD
        assert i.tip_dia < i.pitch_dia < i.root_dia

    def test_space_loop_closed(self) -> None:
        rg = c.ring_gear_tooth_space(2, 40, 90)
        assert c.loop_is_closed(list(rg.segments))

    def test_thin_rim_warns(self) -> None:
        rg = c.ring_gear_tooth_space(2, 40, 85.5)  # barely clears root 85
        assert any("rim" in w for w in rg.warnings)


class TestSprocket:
    def test_iso606_pitch_diameter(self) -> None:
        # PD = p / sin(180°/z)
        sp = c.sprocket_tooth("08B", 17)
        i = sp.invariants
        assert i.pitch == 12.7
        assert i.pitch_dia == pytest.approx(12.7 / math.sin(math.pi / 17))

    def test_seating_radius_from_roller(self) -> None:
        sp = c.sprocket_tooth("10B", 25)
        assert sp.invariants.seating_radius == pytest.approx(0.505 * 10.16)

    def test_tip_diameter_formula(self) -> None:
        sp = c.sprocket_tooth("08B", 17)
        expected = 12.7 * (0.6 + 1.0 / math.tan(math.pi / 17))
        assert sp.invariants.tip_dia == pytest.approx(expected)

    def test_gap_loop_closed(self) -> None:
        for chain, z in (("08B", 17), ("10B", 25), ("12B", 38)):
            sp = c.sprocket_tooth(chain, z)
            assert c.loop_is_closed(list(sp.segments), tol=1e-4), f"{chain} z{z}"

    def test_unknown_chain_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown chain"):
            c.sprocket_tooth("99Z", 20)


class TestRevolveEnvelope:
    def test_tube_annulus(self) -> None:
        env = c.revolve_envelope(
            [(30, 0), (30, 10), (15, 10), (15, 0)], 0, 1, "y"
        )
        assert env.max_radius == 30.0
        assert env.min_radius == 15.0
        assert env.axial_min == 0.0 and env.axial_max == 10.0

    def test_solid_min_radius_zero(self) -> None:
        env = c.revolve_envelope([(0, 0), (20, 0), (0, 5)], 0, 1, "y")
        assert env.min_radius == 0.0
        assert env.max_radius == 20.0


class TestHelix:
    def test_revolutions_from_pitch(self) -> None:
        h = c.helix_spec(10, 1.5, 30)
        assert h.revolutions == pytest.approx(20.0)

    def test_pitch_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="pitch"):
            c.helix_spec(10, 0, 20)
