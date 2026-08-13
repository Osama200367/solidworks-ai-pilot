"""Twin tests for curved features: envelope, revolve, mesh, graceful checks."""


import pytest

from swpilot.model import curves as cv
from swpilot.model.tracker import ModelError, ModelTracker


def build_gear(m: float = 2, z: int = 20, a: float = 20.0, fw: float = 20.0) -> ModelTracker:
    tp = cv.spur_gear_tooth(m, z, a)
    t = ModelTracker()
    t.new_part()
    t.create_sketch("front")
    t.draw_circle((0, 0), tp.invariants.root_dia)
    t.extrude(fw, False)
    t.create_sketch("front")
    for seg in tp.segments:
        if isinstance(seg, cv.SplineSeg):
            t.draw_spline(list(seg.points), "gear_tooth")
        elif isinstance(seg, cv.ArcSeg):
            t.draw_arc(seg.center, seg.start, seg.end, seg.ccw, "gear_tooth")
        else:
            t.draw_line(seg.start, seg.end, "gear_tooth")
    tooth = t.extrude(fw, False)
    t.create_axis("z")
    t.circular_pattern([tooth.name], "z", z, 360.0, True)
    t.set_gear(tp.invariants)
    return t


class TestCurvedSketch:
    def test_curve_entities_reject_prismatic_mix(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 0), 10)
        with pytest.raises(ModelError, match="cannot share a sketch"):
            t.draw_spline([(0, 0), (5, 5)], "x")

    def test_prismatic_entities_reject_curve_mix(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_line((0, 0), (5, 5))
        with pytest.raises(ModelError, match="cannot share"):
            t.draw_circle((0, 0), 10)


class TestGearEnvelope:
    def test_patterned_gear_bounds_the_tip_cylinder(self) -> None:
        t = build_gear(2, 20, 20, 20)
        pat = next(f for f in t.features if f.kind == "circular_pattern")
        assert pat.curve_full_disk
        lo, hi = t.feature_aabb(pat.name)
        # tip diameter 44 → ±22 in-plane, face width 20 along z
        assert lo == pytest.approx((-22.0, -22.0, 0.0))
        assert hi == pytest.approx((22.0, 22.0, 20.0))

    def test_solid_features_include_root_tooth_and_pattern(self) -> None:
        t = build_gear()
        assert t.solid_features() == ["Boss-Extrude1", "Boss-Extrude2", "CirPattern1"]

    def test_gear_meta_stored(self) -> None:
        t = build_gear(2, 20, 20)
        assert t.gear is not None
        assert t.gear.pitch_dia == 40.0


class TestRevolve:
    def test_solid_of_revolution_envelope(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_axis("x")
        t.create_sketch("front")
        # a tube half-section, all radii > 0, about x
        for a, b in (((-8, 10), (8, 10)), ((8, 10), (8, 40)), ((8, 40), (-8, 40)),
                     ((-8, 40), (-8, 10))):
            t.draw_line(a, b)
        f = t.revolve("x", 360.0, False)
        assert f.curved and f.curve_radius == pytest.approx(40.0)
        lo, hi = t.feature_aabb(f.name)
        # about x: radius 40 in y and z, axial x in [-8, 8]
        assert lo[0] == pytest.approx(-8.0) and hi[0] == pytest.approx(8.0)
        assert hi[1] == pytest.approx(40.0) and hi[2] == pytest.approx(40.0)

    def test_profile_crossing_axis_rejected(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_axis("x")
        t.create_sketch("front")
        for a, b in (((-8, -8), (8, -8)), ((8, -8), (8, 8)), ((8, 8), (-8, 8)),
                     ((-8, 8), (-8, -8))):
            t.draw_line(a, b)
        with pytest.raises(ModelError, match="crosses the revolve axis"):
            t.revolve("x", 360.0, False)

    def test_axis_normal_to_plane_rejected(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((10, 0), 5)
        with pytest.raises(ModelError, match="must lie IN the sketch plane"):
            t.revolve("z", 360.0, False)  # z is the front-plane normal

    def test_partial_revolve_warns(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_axis("x")
        t.create_sketch("front")
        t.draw_circle((0, 20), 5)  # circle off the axis → torus section
        t.revolve("x", 180.0, False)
        assert any("partial revolve" in w for w in t.pop_warnings())

    def test_revolve_requires_axis(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 20), 5)
        with pytest.raises(ModelError, match="axis"):
            t.revolve("x", 360.0, False)


class TestCurvedCut:
    def test_curved_cut_warns_windows_verified(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 0), 80)
        t.extrude(20, False)
        t.pop_warnings()
        t.create_sketch("front")
        # a curved tooth-space loop (reuse ring gear space)
        rg = cv.ring_gear_tooth_space(2, 40, 90)
        for seg in rg.segments:
            if isinstance(seg, cv.SplineSeg):
                t.draw_spline(list(seg.points), "ring_space")
            else:
                t.draw_arc(seg.center, seg.start, seg.end, seg.ccw, "ring_space")
        t.cut_extrude(True, None, False, None)
        assert any("curved cut" in w and "Windows-verified" in w for w in t.pop_warnings())


class TestOpenLoopWarning:
    def test_unclosed_curve_loop_warns(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_spline([(10, 0), (10, 10), (0, 10)], "x")  # open, single spline
        t.extrude(5, False)
        assert any("does not close" in w for w in t.pop_warnings())


class TestHelix:
    def test_cosmetic_helix_records_feature(self) -> None:
        t = ModelTracker()
        t.new_part()
        t.create_sketch("front")
        t.draw_circle((0, 0), 16)
        t.extrude(40, False)
        t.pop_warnings()
        f = t.helix_thread(16, 2, 30, True)
        assert f.kind == "helix"
        assert any("cosmetic" in w for w in t.pop_warnings())

    def test_helix_needs_solid(self) -> None:
        t = ModelTracker()
        t.new_part()
        with pytest.raises(ModelError, match="no solid"):
            t.helix_thread(16, 2, 30, True)


class TestMesh:
    def test_matching_gears_mesh(self) -> None:
        a = cv.spur_gear_tooth(2, 20, 20).invariants
        b = cv.spur_gear_tooth(2, 40, 20).invariants
        r = cv.check_mesh(a, b)
        assert r.meshes and r.center_distance == 60.0

    def test_module_mismatch_fails(self) -> None:
        a = cv.spur_gear_tooth(2, 20, 20).invariants
        b = cv.spur_gear_tooth(3, 40, 20).invariants
        r = cv.check_mesh(a, b)
        assert not r.meshes and any("module" in x for x in r.reasons)

    def test_pressure_angle_mismatch_fails(self) -> None:
        a = cv.spur_gear_tooth(2, 20, 20).invariants
        b = cv.spur_gear_tooth(2, 40, 14.5).invariants
        r = cv.check_mesh(a, b)
        assert not r.meshes and any("pressure" in x for x in r.reasons)


class TestAssemblyEnvelope:
    def test_gear_component_world_aabb_is_tip_cylinder(self) -> None:
        from swpilot.model.assembly import AssemblyTracker
        from swpilot.model.transforms import build_transform

        gear = build_gear(2, 20, 20, 20)
        gear.save_part("g.SLDPRT")
        asm = AssemblyTracker("a")
        asm.insert_component(
            "g_1", "g", gear, None, build_transform([], (0, 0, 0)),
            fixed=True, saved_path="g.SLDPRT",
        )
        lo, hi = asm.component("g_1").world_aabb()
        assert hi[0] - lo[0] == pytest.approx(44.0)  # tip diameter
        assert hi[1] - lo[1] == pytest.approx(44.0)
