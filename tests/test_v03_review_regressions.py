# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning fixes from the v0.3 adversarial review.

9 findings were confirmed and 6 more adjudicated inline; each test class
pins one (or one tightly-coupled group). The ByRef-VARIANT and
title-refresh fixes live in the COM backend (unimportable in CI) and are
covered by the WINDOWS_SETUP checklist instead.
"""

import pytest

from swpilot.backends import calls
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import CommandFileError, expand_commands, parse_command_data
from swpilot.commands.schema import InsertComponent, Mate
from swpilot.executor import execute
from swpilot.model.assembly import AssemblyTracker
from swpilot.model.tracker import ModelError, ModelTracker
from swpilot.model.transforms import RotationStep, build_transform
from tests.test_assembly import asm_with, make_plate_part


class TestPreSolveMatePicks:
    """Selections run at pre-mate positions: AddMate5 does the moving."""

    def test_displaced_component_picks_use_insert_position(self) -> None:
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 30)),
        )
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        pick_a, pick_b = asm.mate_picks(a, b)
        assert pick_a[2] == pytest.approx(30.0)  # cover bottom, pre-solve
        assert pick_b[2] == pytest.approx(10.0)  # base top
        asm.mate("coincident", a, b, None)
        # the solver still snapped the twin afterwards
        assert asm.component("cover_1").transform.translation[2] == pytest.approx(10.0)


class TestCoplanarPickDisambiguation:
    """Exact-position inserts: picks must land where only one face exists."""

    def test_bolt_seat_pick_lands_in_clearance_ring(self) -> None:
        bolt = ModelTracker()
        bolt.new_part()
        bolt.create_sketch("front")
        bolt.draw_circle((0, 0), 13)
        bolt.extrude(8, False)
        bolt.create_sketch("front")
        bolt.draw_circle((0, 0), 8)
        bolt.extrude(20, True)  # shank [-20, 0]
        bolt.save_part("b.SLDPRT")
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("bolt_1", bolt, (-30, 0, 10)),  # head seat exactly on base top
        )
        seat = asm.resolve_face("base_1", "+z", None)
        head = asm.resolve_face("bolt_1", "-z", "Boss-Extrude1")
        pick_seat, pick_head = asm.mate_picks(seat, head)
        # the head pick sits in the shank/hole clearance ring over the base's
        # Ø9 hole at (-30, 0): between r=4 (shank) and r=4.5 (hole)
        r = ((pick_head[0] + 30.0) ** 2 + pick_head[1] ** 2) ** 0.5
        assert 4.0 < r < 4.5
        assert pick_seat != pick_head

    def test_identical_overlapping_plates_warn(self) -> None:
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 10)),  # exactly coplanar
        )
        a = asm.resolve_face("cover_1", "-z", None)
        b = asm.resolve_face("base_1", "+z", None)
        asm.pop_warnings()
        asm.mate_picks(a, b)
        assert any("coplanar" in w and "standoff" in w for w in asm.pop_warnings())


class TestTransformArrayConvention:
    """IMathTransform ArrayData rows are images of the local axes (R^T)."""

    def test_z90_arraydata_first_row_is_plus_y(self) -> None:
        t = build_transform([RotationStep("z", 90)], (0.0, 0.0, 0.0))
        specs = calls.component_transform_calls("c", t.to_row_major(), (0, 0, 0))
        data16 = specs[0].value
        assert data16[0:3] == (0.0, 1.0, 0.0)  # image of local X under z+90
        assert data16[12] == 1.0  # scale


class TestDistanceMateLimits:
    def test_limit_slots_carry_the_distance(self) -> None:
        specs = calls.add_mate_calls("distance", (0, 0, 0), (0, 0, 5), 3.0, "Mate1")
        mate = [c for c in specs if c.method == "AddMate5"][0]
        assert mate.args[3] == mate.args[4] == mate.args[5] == pytest.approx(0.003)


class TestExternalComponentPreload:
    def test_mock_logs_opendoc6_before_insert(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.3",
                "commands": [
                    {"op": "new_assembly", "name": "asm"},
                    {
                        "op": "insert_component",
                        "file": "vendor/widget.SLDPRT",
                        "name": "w_1",
                        "envelope": [30, 20, 10],
                    },
                ],
            }
        )
        backend = MockBackend()
        report = execute(expand_commands(list(cf.commands)), backend)
        assert report.success
        methods = [c.method for c in backend.call_log]
        open_i = methods.index("OpenDoc6")
        insert_i = methods.index("AddComponent5")
        assert open_i < insert_i
        assert methods[open_i + 1] == "ActivateDoc2"  # back to the assembly
        open_spec = backend.call_log[open_i]
        assert open_spec.args[1] == calls.SW_DOC_PART
        assert open_spec.args[4:] == (0, 0)  # ByRef Errors/Warnings slots


class TestMovablePrefersFreerComponent:
    def test_pinned_component_not_dragged_when_free_one_available(self) -> None:
        asm = asm_with(
            ("base_1", make_plate_part(), (0, 0, 0)),
            ("cover_1", make_plate_part(t=6), (0, 0, 30)),
            ("bracket_1", make_plate_part(t=4), (0, 0, 60)),
        )
        # pin cover in z via a seat mate
        asm.mate(
            "coincident",
            asm.resolve_face("cover_1", "-z", None),
            asm.resolve_face("base_1", "+z", None),
            None,
        )
        cover_z = asm.component("cover_1").transform.translation[2]
        # now mate bracket (3 free axes) against cover (2 free): with the
        # bracket entity as 'a' and cover as 'b', the FREER bracket must move
        asm.mate(
            "coincident",
            asm.resolve_face("bracket_1", "-z", None),
            asm.resolve_face("cover_1", "+z", None),
            None,
        )
        assert asm.component("cover_1").transform.translation[2] == cover_z
        assert asm.component("bracket_1").transform.translation[2] == pytest.approx(
            cover_z + 6.0
        )

    def test_both_fixed_mate_rejected(self) -> None:
        asm = AssemblyTracker("a")
        for name, at in (("p_1", (0.0, 0.0, 0.0)), ("p_2", (0.0, 0.0, 30.0))):
            asm.insert_component(
                name, "p", make_plate_part(), None,
                build_transform([], at), fixed=True, saved_path="p.SLDPRT",
            )
        a = asm.resolve_face("p_2", "-z", None)
        b = asm.resolve_face("p_1", "+z", None)
        with pytest.raises(ModelError, match="both components are fixed"):
            asm.mate("coincident", a, b, None)


class TestFacePickValidity:
    def test_pick_avoids_gap_between_disjoint_bosses(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((-30, 0), 20, 20)
        part.extrude(10, False)
        part.create_sketch("front")
        part.draw_rectangle((30, 0), 20, 20)
        part.extrude(10, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        asm = asm_with(("p_1", part, (0, 0, 0)))
        face = asm.resolve_face("p_1", "+z", None)
        # the AABB center (0, 0) lies in the void between the bosses
        assert abs(face.pick[0]) > 10.0

    def test_pick_avoids_counterbore_opening(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 40, 40)
        part.extrude(10, False)
        part.create_plane("Top1", "front", 10.0)
        part.create_sketch("Top1")
        part.draw_circle((0, 0), 16)
        part.cut_extrude(False, 4, True, None)  # counterbore opening on top
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        asm = asm_with(("p_1", part, (0, 0, 0)))
        face = asm.resolve_face("p_1", "+z", None)
        r = (face.pick[0] ** 2 + face.pick[1] ** 2) ** 0.5
        assert r > 8.0  # outside the Ø16 opening


class TestBoltCircleDfm:
    BASE = [
        {"op": "new_part", "name": "plate"},
        {"op": "create_sketch"},
        {"op": "draw_rectangle", "width": 100, "height": 50},
        {"op": "extrude", "depth": 10},
        {"op": "hole", "at": [[-30, 0], [30, 0]], "diameter": 9},
        {"op": "save_part", "path": "plate.SLDPRT"},
    ]

    def _bolt(self, head_d: float) -> list[dict]:
        return [
            {"op": "new_part", "name": "bolt"},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": head_d},
            {"op": "extrude", "depth": 8},
            {"op": "create_sketch"},
            {"op": "draw_circle", "diameter": 8},
            {"op": "extrude", "depth": 20, "reverse": True},
            {"op": "save_part", "path": "bolt.SLDPRT"},
        ]

    def _asm(self) -> list[dict]:
        return [
            {"op": "new_assembly", "name": "a"},
            {"op": "insert_component", "part": "plate", "name": "plate_1"},
            {
                "op": "bolt_circle",
                "bolt": {
                    "part": "bolt",
                    "shank_feature": "Boss-Extrude2",
                    "head_feature": "Boss-Extrude1",
                },
                "holes": {"component": "plate_1", "of_feature": "Cut-Extrude1"},
                "seat": {"component": "plate_1", "facing": "+z"},
            },
        ]

    def test_head_smaller_than_hole_rejected(self) -> None:
        cf = parse_command_data(
            {"schema_version": "0.3", "commands": self.BASE + self._bolt(8.5) + self._asm()}
        )
        with pytest.raises(CommandFileError, match="would fall through"):
            expand_commands(list(cf.commands))

    def test_bolt_is_the_mate_mover(self) -> None:
        cf = parse_command_data(
            {"schema_version": "0.3", "commands": self.BASE + self._bolt(13) + self._asm()}
        )
        expanded = expand_commands(list(cf.commands))
        mates = [ec.command for ec in expanded if isinstance(ec.command, Mate)]
        # static plate side is always entity 'a'; the bolt ('b') moves
        assert all(m.a.component == "plate_1" for m in mates)

    def test_bolts_inserted_with_standoff(self) -> None:
        cf = parse_command_data(
            {"schema_version": "0.3", "commands": self.BASE + self._bolt(13) + self._asm()}
        )
        expanded = expand_commands(list(cf.commands))
        inserts = [
            ec.command
            for ec in expanded
            if isinstance(ec.command, InsertComponent) and ec.command.part == "bolt"
        ]
        assert all(i.at[2] == pytest.approx(12.0) for i in inserts)  # 10 + 2 standoff
