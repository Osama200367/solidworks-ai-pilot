# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""End-to-end acceptance for v0.5: gears, mesh check, revolved pulley.

The mock call plan is byte-identical to what the COM backend executes
(shared CallSpec builders); spline/revolve/helix FIDELITY is Windows-only.
"""

import math
from pathlib import Path

import pytest

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute

EXAMPLES = Path(__file__).parent.parent / "examples"


def run(name: str):
    _, expanded = load_and_expand(EXAMPLES / name)
    backend = MockBackend()
    report = execute(expanded, backend, schema_version="0.5")
    assert report.success, [r.error for r in report.results if r.error]
    return report, backend


class TestSpurGear:
    def test_build_sequence(self) -> None:
        _, b = run("spur_gear_m2_z20.json")
        methods = [c.method for c in b.call_log]
        # root cylinder, one tooth (2 splines + arcs), pattern, bore, keyway
        assert methods.count("CreateSpline") == 2  # two involute flanks
        assert methods.count("CreateArc") == 4  # 2 root fillets + tip land + root base
        assert methods.count("FeatureCircularPattern4") == 1
        assert methods.count("CreateCircleByRadius") == 2  # root cylinder + bore
        # circular pattern of 20 teeth
        cp = next(c for c in b.call_log if c.method == "FeatureCircularPattern4")
        assert cp.args[0] == 20

    def test_spline_points_in_meters(self) -> None:
        _, b = run("spur_gear_m2_z20.json")
        spline = next(c for c in b.call_log if c.method == "CreateSpline")
        pts = spline.args[0]
        assert len(pts) == 18 * 3  # 18 points × (x, y, z)
        # every point within the root/tip annulus (17.5–22 mm → m)
        for i in range(0, len(pts), 3):
            r = math.hypot(pts[i], pts[i + 1])
            assert 0.0174 <= r <= 0.0221

    def test_gear_invariants_reported(self) -> None:
        report, _ = run("spur_gear_m2_z20.json")
        # the twin's gear meta is on the part (checked via mesh in the pair
        # example); here confirm the part built its full feature tree
        part = report.final_state["documents"][0]
        names = [f["name"] for f in part["features"]]
        assert names == [
            "Boss-Extrude1", "Boss-Extrude2", "CirPattern1",
            "Cut-Extrude1", "Cut-Extrude2",  # bore + keyway
        ]

    def test_keyway_cut_present(self) -> None:
        _, b = run("spur_gear_m2_z20.json")
        rects = [c for c in b.call_log if c.method == "CreateCenterRectangle"]
        assert len(rects) == 1  # the keyway


class TestGearMesh:
    def test_mesh_passes_at_correct_center_distance(self) -> None:
        report, _ = run("gear_mesh_check.json")
        # gear_mesh_check emits no primitives; success means it validated
        assert report.success
        asm = report.final_state["documents"][-1]
        assert asm["kind"] == "assembly"
        names = [c["name"] for c in asm["components"]]
        assert names == ["pinion_1", "wheel_1"]

    def test_two_gears_two_patterns(self) -> None:
        _, b = run("gear_mesh_check.json")
        patterns = [c for c in b.call_log if c.method == "FeatureCircularPattern4"]
        assert [p.args[0] for p in patterns] == [20, 40]

    def test_mismatched_module_rejected(self) -> None:
        from swpilot.commands.loader import CommandFileError, expand_commands, parse_command_data

        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "involute_spur_gear", "name": "a", "module": 2,
                     "teeth": 20, "face_width": 10, "bore": 8},
                    {"op": "save_part", "path": "a.SLDPRT"},
                    {"op": "involute_spur_gear", "name": "b", "module": 3,
                     "teeth": 20, "face_width": 10, "bore": 8},
                    {"op": "save_part", "path": "b.SLDPRT"},
                    {"op": "new_assembly", "name": "asm"},
                    {"op": "insert_component", "part": "a", "name": "a_1", "fixed": True},
                    {"op": "insert_component", "part": "b", "name": "b_1", "at": [50, 0, 0]},
                    {"op": "gear_mesh_check", "a": "a_1", "b": "b_1"},
                ],
            }
        )
        with pytest.raises(CommandFileError, match="do not mesh"):
            expand_commands(list(cf.commands))

    def test_wrong_center_distance_rejected(self) -> None:
        from swpilot.commands.loader import (
            CommandFileError,
            expand_commands,
            parse_command_data,
        )

        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "involute_spur_gear", "name": "a", "module": 2,
                     "teeth": 20, "face_width": 10, "bore": 8},
                    {"op": "save_part", "path": "a.SLDPRT"},
                    {"op": "involute_spur_gear", "name": "b", "module": 2,
                     "teeth": 40, "face_width": 10, "bore": 8},
                    {"op": "save_part", "path": "b.SLDPRT"},
                    {"op": "new_assembly", "name": "asm"},
                    {"op": "insert_component", "part": "a", "name": "a_1", "fixed": True},
                    {"op": "insert_component", "part": "b", "name": "b_1", "at": [60, 0, 0]},
                    {"op": "gear_mesh_check", "a": "a_1", "b": "b_1",
                     "expected_center_distance": 55},
                ],
            }
        )
        with pytest.raises(CommandFileError, match="center distance"):
            expand_commands(list(cf.commands))


class TestRevolvedPulley:
    def test_revolve_call(self) -> None:
        _, b = run("v_pulley_revolved.json")
        rev = [c for c in b.call_log if c.method == "FeatureRevolve2"]
        assert len(rev) == 1
        # 360° = 2π radians in the Dir1Angle slot (index 8)
        assert rev[0].args[8] == pytest.approx(2 * math.pi)
        # axis selected as a reference axis
        axis_sel = [
            c for c in b.call_log
            if c.method == "SelectByID2" and c.args[1] == "AXIS"
        ]
        assert axis_sel and axis_sel[0].args[0] == "SWPilot_Axis_X"

    def test_profile_lines_then_revolve(self) -> None:
        _, b = run("v_pulley_revolved.json")
        methods = [c.method for c in b.call_log]
        assert methods.count("CreateLine") == 7  # the half-section outline
        assert methods.index("FeatureRevolve2") > methods.index("CreateLine")

    def test_bore_after_revolve(self) -> None:
        report, _ = run("v_pulley_revolved.json")
        names = [f["name"] for f in report.final_state["documents"][0]["features"]]
        assert names == ["Boss-Extrude1", "Cut-Extrude1"]  # revolve boss + bore


class TestSprocketAndRing:
    def test_sprocket_iso_builds(self) -> None:
        from swpilot.commands.loader import expand_commands, parse_command_data

        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "sprocket_iso", "chain": "08B", "teeth": 17,
                     "face_width": 8, "bore": 20},
                    {"op": "save_part", "path": "sprk.SLDPRT"},
                ],
            }
        )
        b = MockBackend()
        report = execute(expand_commands(list(cf.commands)), b)
        assert report.success
        cp = [c for c in b.call_log if c.method == "FeatureCircularPattern4"]
        assert cp[0].args[0] == 17

    def test_ring_gear_builds(self) -> None:
        from swpilot.commands.loader import expand_commands, parse_command_data

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
        b = MockBackend()
        report = execute(expand_commands(list(cf.commands)), b)
        assert report.success
        # rim tube (cut Ø tip) + one space cut + pattern
        cp = [c for c in b.call_log if c.method == "FeatureCircularPattern4"]
        assert cp[0].args[0] == 40


class TestHelixThread:
    def test_cosmetic_helix_call(self) -> None:
        from swpilot.commands.loader import expand_commands, parse_command_data

        cf = parse_command_data(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "create_plate", "width": 20, "height": 20, "thickness": 50},
                    {"op": "helix_thread", "diameter": 16, "pitch": 2, "length": 40},
                    {"op": "save_part", "path": "stud.SLDPRT"},
                ],
            }
        )
        b = MockBackend()
        report = execute(expand_commands(list(cf.commands)), b)
        assert report.success
        helix = next(c for c in b.call_log if c.method == "InsertHelix")
        assert helix.target == "Model"  # IModelDoc2.InsertHelix, not FeatureManager
        # revolutions = length / pitch = 20; pitch in meters
        assert helix.args[7] == pytest.approx(20.0)
        assert helix.args[2] is False  # Clockwise = not right_handed (RH helix)
        assert helix.args[6] == pytest.approx(0.002)  # pitch 2 mm
        # the base-circle sketch is opened and closed around the circle
        methods = [c.method for c in b.call_log]
        assert methods.count("InsertSketch") >= 2  # open + close the helix sketch
        assert any(
            "cosmetic" in w for r in report.results for w in r.warnings
        )
