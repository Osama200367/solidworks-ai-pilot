"""AssemblyTracker tests: components, entity resolution, the snap-solver."""

import pytest

from swpilot.model.assembly import AssemblyTracker
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import ModelError, ModelTracker
from swpilot.model.transforms import RotationStep, build_transform


def make_plate_part(w: float = 100, h: float = 50, t: float = 10) -> ModelTracker:
    tr = ModelTracker()
    tr.new_part()
    tr.create_sketch("front")
    tr.draw_rectangle((0, 0), w, h)
    tr.extrude(t, False)
    tr.create_sketch("front")
    tr.draw_circle((-30, 0), 9)
    tr.draw_circle((30, 0), 9)
    tr.cut_extrude(True, None, False, None)
    tr.save_part("plate.SLDPRT")
    tr.pop_warnings()
    return tr


def asm_with(*parts: tuple[str, ModelTracker, tuple[float, float, float]]) -> AssemblyTracker:
    asm = AssemblyTracker("asm")
    for name, part, at in parts:
        asm.insert_component(
            name=name,
            source=name.rsplit("_", 1)[0],
            part=part,
            envelope=None,
            transform=build_transform([], at),
            fixed=False,
            saved_path="x.SLDPRT",
        )
    asm.pop_warnings()
    return asm


class TestInsert:
    def test_first_component_auto_fixed(self) -> None:
        asm = AssemblyTracker("a")
        rec = asm.insert_component(
            "base_1", "base", make_plate_part(), None, build_transform([], (0, 0, 0)),
            fixed=False, saved_path="p.SLDPRT",
        )
        assert rec.fixed is True
        assert any("automatically fixed" in w for w in asm.pop_warnings())

    def test_unsaved_part_rejected(self) -> None:
        part = ModelTracker()
        part.new_part()
        asm = AssemblyTracker("a")
        with pytest.raises(ModelError, match="has not been saved"):
            asm.insert_component(
                "c_1", "c", part, None, build_transform([], (0, 0, 0)),
                fixed=False, saved_path=None,
            )

    def test_duplicate_instance_name_rejected(self) -> None:
        part = make_plate_part()
        asm = asm_with(("base_1", part, (0, 0, 0)))
        with pytest.raises(ModelError, match="already exists"):
            asm.insert_component(
                "base_1", "base", part, None, build_transform([], (0, 0, 0)),
                fixed=False, saved_path="p.SLDPRT",
            )

    def test_external_without_envelope_warns(self) -> None:
        asm = AssemblyTracker("a")
        asm.insert_component(
            "ext_1", "ext.SLDPRT", None, None, build_transform([], (0, 0, 0)),
            fixed=True, saved_path="ext.SLDPRT",
        )
        assert any("no declared envelope" in w for w in asm.pop_warnings())


class TestResolveFace:
    def test_world_face_of_translated_component(self) -> None:
        asm = asm_with(("base_1", make_plate_part(), (0, 0, 0)),
                       ("cover_1", make_plate_part(), (0, 0, 12)))
        face = asm.resolve_face("cover_1", "+z", None)
        assert face.axis == 2 and face.position == pytest.approx(22.0)

    def test_rotated_component_face(self) -> None:
        part = make_plate_part()
        asm = AssemblyTracker("a")
        asm.insert_component(
            "flip_1", "p", part, None,
            build_transform([RotationStep("x", 180)], (0, 0, 0)),
            fixed=True, saved_path="p.SLDPRT",
        )
        # flipped about x: the local +z (10mm) face now faces -z at -10
        face = asm.resolve_face("flip_1", "-z", None)
        assert face.position == pytest.approx(-10.0)

    def test_face_pick_dodges_holes(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 40, 40)
        part.extrude(10, False)
        part.create_sketch("front")
        part.draw_circle((0, 0), 20)  # big center hole
        part.cut_extrude(True, None, False, None)
        part.save_part("p.SLDPRT")
        asm = asm_with(("p_1", part, (0, 0, 0)))
        face = asm.resolve_face("p_1", "+z", None)
        px, py, _ = face.pick
        assert (px**2 + py**2) ** 0.5 > 10.0  # outside the hole

    def test_envelope_face_warns_declared(self) -> None:
        asm = AssemblyTracker("a")
        asm.insert_component(
            "ext_1", "ext.SLDPRT", None, (60.0, 40.0, 5.0),
            build_transform([], (0, 0, 0)), fixed=True, saved_path="ext.SLDPRT",
        )
        asm.pop_warnings()
        face = asm.resolve_face("ext_1", "+z", None)
        assert face.position == pytest.approx(5.0)
        assert any("DECLARED envelope" in w for w in asm.pop_warnings())


class TestResolveCylinder:
    def test_at_disambiguates(self) -> None:
        asm = asm_with(("base_1", make_plate_part(), (0, 0, 0)))
        cyl = asm.resolve_cylinder("base_1", "Cut-Extrude1", (30, 0))
        assert cyl.center[0] == pytest.approx(30.0)
        assert cyl.radius == pytest.approx(4.5)
        assert cyl.feature_kind == "cut"

    def test_multi_circle_without_at_rejected(self) -> None:
        asm = asm_with(("base_1", make_plate_part(), (0, 0, 0)))
        with pytest.raises(ModelError, match="disambiguate with 'at'"):
            asm.resolve_cylinder("base_1", "Cut-Extrude1", None)

    def test_non_circular_feature_rejected(self) -> None:
        asm = asm_with(("base_1", make_plate_part(), (0, 0, 0)))
        with pytest.raises(ModelError, match="no circles"):
            asm.resolve_cylinder("base_1", "Boss-Extrude1", None)


class TestSolver:
    def _stack(self) -> AssemblyTracker:
        return asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 10)),
        )

    def test_coincident_snaps_and_pins(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        asm.mate("coincident", a, b, None)
        cover = asm.component("cover_1")
        assert cover.transform.translation[2] == pytest.approx(10.0)
        assert 2 in cover.pinned

    def test_distance_offsets_along_static_normal(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        asm.mate("distance", a, b, 3.0)
        assert asm.component("cover_1").transform.translation[2] == pytest.approx(13.0)

    def test_concentric_pins_cross_axes(self) -> None:
        asm = self._stack()
        a = asm.resolve_cylinder("cover_1", "Cut-Extrude1", (-30, 0))
        b = asm.resolve_cylinder("base_1", "Cut-Extrude1", (-30, 0))
        asm.mate("concentric", a, b, None)
        cover = asm.component("cover_1")
        assert 0 in cover.pinned and 1 in cover.pinned and 2 not in cover.pinned

    def test_mismatched_hole_pattern_over_constrained(self) -> None:
        base = make_plate_part()
        shifted = ModelTracker()
        shifted.new_part()
        shifted.create_sketch("front")
        shifted.draw_rectangle((0, 0), 100, 50)
        shifted.extrude(6, False)
        shifted.create_sketch("front")
        shifted.draw_circle((-30, 0), 9)
        shifted.draw_circle((32, 0), 9)  # 2mm off the base's pattern
        shifted.cut_extrude(True, None, False, None)
        shifted.save_part("s.SLDPRT")
        asm = asm_with(("base_1", base, (0, 0, 0)), ("cover_1", shifted, (0, 0, 10)))
        asm.mate(
            "concentric",
            asm.resolve_cylinder("cover_1", "Cut-Extrude1", (-30, 0)),
            asm.resolve_cylinder("base_1", "Cut-Extrude1", (-30, 0)),
            None,
        )
        with pytest.raises(ModelError, match="over-constrained.*2.000 mm"):
            asm.mate(
                "concentric",
                asm.resolve_cylinder("cover_1", "Cut-Extrude1", (32, 0)),
                asm.resolve_cylinder("base_1", "Cut-Extrude1", (30, 0)),
                None,
            )

    def test_matching_second_concentric_locks_rotation(self) -> None:
        asm = self._stack()
        for hole in ((-30, 0), (30, 0)):
            asm.mate(
                "concentric",
                asm.resolve_cylinder("cover_1", "Cut-Extrude1", hole),
                asm.resolve_cylinder("base_1", "Cut-Extrude1", hole),
                None,
            )
        assert 2 in asm.component("cover_1").locked_rot

    def test_redundant_mate_warns(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        asm.mate("coincident", a, b, None)
        asm.pop_warnings()
        asm.mate(
            "coincident",
            asm.resolve_face("cover_1", "-z", None),
            asm.resolve_face("base_1", "+z", None),
            None,
        )
        assert any("redundant" in w for w in asm.pop_warnings())

    def test_perpendicular_faces_rejected(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+x", None)
        with pytest.raises(ModelError, match="not parallel"):
            asm.mate("coincident", a, b, None)

    def test_same_component_rejected(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("cover_1", "+z", None)
        with pytest.raises(ModelError, match="both entities belong"):
            asm.mate("coincident", a, b, None)

    def test_width_deferred(self) -> None:
        asm = self._stack()
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        with pytest.raises(ModelError, match="deferred in v0.3"):
            asm.mate("width", a, b, None)

    def test_shank_interference_warns(self) -> None:
        bolt = ModelTracker()
        bolt.new_part()
        bolt.create_sketch("front")
        bolt.draw_circle((0, 0), 9)  # shank as big as the hole
        bolt.extrude(20, False)
        bolt.save_part("b.SLDPRT")
        asm = asm_with(("base_1", make_plate_part(), (0, 0, 0)), ("bolt_1", bolt, (-30, 0, 0)))
        asm.mate(
            "concentric",
            asm.resolve_cylinder("bolt_1", "Boss-Extrude1", None),
            asm.resolve_cylinder("base_1", "Cut-Extrude1", (-30, 0)),
            None,
        )
        assert any("does not clear" in w for w in asm.pop_warnings())


class TestUnderConstraint:
    def test_floating_component_warns_at_save(self) -> None:
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 10)),
        )
        asm.save_assembly("/abs/a.SLDASM")
        assert any("under-constrained" in w for w in asm.pop_warnings())

    def test_fastener_spin_reported_as_normal(self) -> None:
        session = SessionTracker()
        # solver-level scenario mirroring a mated bolt: coincident seat +
        # one concentric -> only spin about z remains
        del session
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 10)),
        )
        asm.mate(
            "coincident",
            asm.resolve_face("cover_1", "-z", None),
            asm.resolve_face("base_1", "+z", None),
            None,
        )
        asm.mate(
            "concentric",
            asm.resolve_cylinder("cover_1", "Cut-Extrude1", (-30, 0)),
            asm.resolve_cylinder("base_1", "Cut-Extrude1", (-30, 0)),
            None,
        )
        asm.pop_warnings()
        asm.save_assembly("/abs/a.SLDASM")
        warnings = asm.pop_warnings()
        assert any("normal for fasteners" in w for w in warnings)
        assert not any("under-constrained" in w for w in warnings)
