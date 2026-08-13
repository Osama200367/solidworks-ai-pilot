# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning fixes from the v0.2 adversarial review.

19 findings were confirmed across two verification passes; each test
class pins one (or one tightly-coupled group). If any of these regress,
the failure mode they describe returns — most of them on the first real
Windows run.
"""

import math

import pytest
from pydantic import ValidationError

from swpilot.backends import calls
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import (
    CommandFileError,
    ExpandedCommand,
    expand_commands,
    parse_command_data,
)
from swpilot.commands.schema import CommandFile, CreateSketch, CutExtrude, Hole
from swpilot.executor import execute
from swpilot.model.tracker import ModelError, ModelTracker

# ---------------------------------------------------------------------------
# COM call-plan signatures (critical: these crash on first Windows use)
# ---------------------------------------------------------------------------


class TestFeatureFillet3Signature:
    """FeatureFillet3 takes 14 parameters: 7 scalars + 7 VARIANT arrays."""

    def test_fourteen_args_with_null_arrays(self) -> None:
        specs = calls.fillet_calls([(0, 0, 0)], 2, "Fillet1")
        spec = [c for c in specs if c.method == "FeatureFillet3"][0]
        assert len(spec.args) == 14
        assert spec.args[0] == 195  # recorded-macro options for a plain fillet
        assert spec.args[1] == pytest.approx(0.002)  # R1 in meters
        assert spec.args[7:] == (None,) * 7  # the seven VARIANT arrays


class TestPatternSignatures:
    """FeatureLinearPattern4 takes 20 parameters; FeatureCircularPattern4 takes 7."""

    def test_linear_pattern_twenty_args(self) -> None:
        spec = [
            c
            for c in calls.linear_pattern_calls(["F1"], "SWPilot_Axis_X", False, 20, 3, None, "L1")
            if c.method == "FeatureLinearPattern4"
        ][0]
        assert len(spec.args) == 20
        assert spec.args[14] is True and spec.args[15] is True  # spacing+instances mode

    def test_circular_pattern_seven_args(self) -> None:
        spec = [
            c
            for c in calls.circular_pattern_calls(["F1"], "SWPilot_Axis_Z", 4, 360, True, "C1")
            if c.method == "FeatureCircularPattern4"
        ][0]
        assert len(spec.args) == 7
        assert spec.args[6] is False  # VarySketch


class TestCircularPatternSpacingSemantics:
    """Spacing is per-instance when EqualSpacing=False; total when True."""

    def test_unequal_spacing_converts_total_angle(self) -> None:
        spec = [
            c
            for c in calls.circular_pattern_calls(["F1"], "SWPilot_Axis_Z", 3, 90, False, "C1")
            if c.method == "FeatureCircularPattern4"
        ][0]
        # total 90 deg over count-1 gaps -> 45 deg per instance
        assert spec.args[1] == pytest.approx(math.radians(45))

    def test_equal_spacing_passes_total(self) -> None:
        spec = [
            c
            for c in calls.circular_pattern_calls(["F1"], "SWPilot_Axis_Z", 3, 360, True, "C1")
            if c.method == "FeatureCircularPattern4"
        ][0]
        assert spec.args[1] == pytest.approx(math.radians(360))


class TestRefPlaneRename:
    """InsertRefPlane returns an IRefPlane without .Name — rename must go
    through FeatureByPositionReverse(0), i.e. remember stays False."""

    def test_insert_ref_plane_not_remembered(self) -> None:
        specs = calls.create_plane_calls("P1", "Front Plane", 12)
        spec = [c for c in specs if c.method == "InsertRefPlane"][0]
        assert spec.remember is False


class TestRefPlaneFlipConstant:
    """swRefPlaneReferenceConstraint_OptionFlip is 256 (512 = OriginOnCurve)."""

    def test_flip_constant(self) -> None:
        assert calls.SW_REF_PLANE_OPTION_FLIP == 256

    def test_negative_offset_uses_flip_bit(self) -> None:
        specs = calls.create_plane_calls("P1", "Front Plane", -12)
        spec = [c for c in specs if c.method == "InsertRefPlane"][0]
        assert spec.args[0] == 8 | 256
        assert spec.args[1] == pytest.approx(0.012)  # magnitude, meters


# ---------------------------------------------------------------------------
# Twin accuracy
# ---------------------------------------------------------------------------


def make_plate(tr: ModelTracker, w: float = 100, h: float = 50, t: float = 10) -> None:
    tr.new_part()
    tr.create_sketch("front")
    tr.draw_rectangle((0, 0), w, h)
    tr.extrude(t, reverse=False)


class TestCutSpanValidation:
    """A cut whose travel cannot reach material must fail, not poison state."""

    def test_through_all_pointing_away_rejected(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_plane("Top1", "front", 10.0)
        tr.create_sketch("Top1")
        tr.draw_circle((0, 0), 8)
        with pytest.raises(ModelError, match="cannot intersect any material"):
            tr.cut_extrude(True, None, False, None)  # up, away from the plate

    def test_blind_reversed_span_outside_material_rejected(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)
        with pytest.raises(ModelError, match="cannot intersect any material"):
            tr.cut_extrude(False, 5, True, None)  # span [-5, 0]: only touches

    def test_blind_span_beyond_material_from_offset_plane_rejected(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_plane("High", "front", 20.0)
        tr.create_sketch("High")
        tr.draw_circle((0, 0), 8)
        with pytest.raises(ModelError, match="cannot intersect any material"):
            tr.cut_extrude(False, 5, False, None)  # span [20, 25] over air

    def test_overdeep_blind_cut_warns_but_passes(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)
        tr.cut_extrude(False, 15, False, None)  # deeper than the 10mm plate
        assert any("extends beyond the material" in w for w in tr.pop_warnings())

    def test_correct_cut_after_failed_direction_still_works(self) -> None:
        # The rejected wrong-direction cut must not be recorded as removal.
        tr = ModelTracker()
        make_plate(tr)
        tr.create_plane("Top1", "front", 10.0)
        tr.create_sketch("Top1")
        tr.draw_circle((0, 0), 8)
        with pytest.raises(ModelError):
            tr.cut_extrude(True, None, False, None)
        tr.active_sketch = None  # sketch stays unconsumed after the failure
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)
        tr.cut_extrude(True, None, False, None)  # the correct cut succeeds


class TestRimDerivationClampedToMaterial:
    """Rim pick coordinates must land on real edges, not nominal spans."""

    def test_overdeep_blind_cut_rims_clamped(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)
        tr.cut_extrude(False, 15, False, None)
        rim_z = {e.group: e.midpoint[2] for e in tr.features[1].edges}
        assert rim_z == {"top_loop": 10.0, "bottom_loop": 0.0}

    def test_through_hole_under_counterbore_rim_at_cb_floor(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_plane("Top1", "front", 10.0)
        tr.create_sketch("Top1")
        tr.draw_circle((0, 0), 11)
        tr.cut_extrude(False, 6, True, None)  # counterbore [4, 10]
        tr.create_sketch("Top1")
        tr.draw_circle((0, 0), 6.6)
        tr.cut_extrude(True, None, True, None)  # through hole
        hole = tr.features[2]
        rim_z = {e.group: e.midpoint[2] for e in hole.edges}
        # the top rim is at the counterbore floor, not the (removed) top face
        assert rim_z["top_loop"] == pytest.approx(4.0)
        assert rim_z["bottom_loop"] == pytest.approx(0.0)


class TestFilletBounds:
    """Per-edge bounds are hard limits; halving applies only to pairs."""

    def test_cylinder_rim_fillet_bounded_by_radius(self) -> None:
        tr = ModelTracker()
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 20)  # r = 10
        tr.extrude(30, False)
        with pytest.raises(ModelError, match="too large"):
            tr.fillet(12, "top_loop", None, None)  # exceeds the 10mm radius
        tr.fillet(8, "top_loop", None, None)  # within radius and depth

    def test_both_loops_together_halve_the_depth_budget(self) -> None:
        tr = ModelTracker()
        make_plate(tr, 100, 50, 10)
        with pytest.raises(ModelError, match="BOTH cap loops"):
            tr.fillet(6, "all", None, None)  # 6 >= 10/2 with both loops selected

    def test_single_corner_relaxed_multiple_corners_halved(self) -> None:
        tr = ModelTracker()
        make_plate(tr, 100, 50, 10)
        with pytest.raises(ModelError, match="selected together"):
            tr.fillet(25, "vertical_corners", None, None)  # 4 corners: < 50/2
        tr2 = ModelTracker()
        make_plate(tr2, 100, 50, 10)
        edges = tr2.resolve_edges("fillet", "vertical_corners", None, None)
        # a single corner alone allows up to the full smaller side
        tr2.fillet(30, None, None, edges[0].midpoint)


class TestPatternedBlindCuts:
    """Blind-pocket pattern instances are not removed-through material."""

    def _pocketed(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("top")
        tr.draw_rectangle((0, 0), 100, 100)
        tr.extrude(10, False)
        tr.create_plane("Lid", "top", 10.0)
        tr.create_sketch("Lid")
        tr.draw_circle((-30, 0), 20)
        tr.cut_extrude(False, 3, True, None)  # 3mm pocket
        tr.create_axis("x")
        tr.linear_pattern(["Cut-Extrude1"], "x", 30, 3, None)
        tr.pop_warnings()

    def test_through_cut_inside_patterned_pocket_allowed(self) -> None:
        tr = ModelTracker()
        self._pocketed(tr)
        tr.create_sketch("Lid")
        tr.draw_circle((0, 0), 8)  # inside pocket instance at (0, 0)
        tr.cut_extrude(True, None, True, None)  # 7mm of stock remains: legal

    def test_patterned_through_holes_still_block_duplicates(self) -> None:
        tr = ModelTracker()
        make_plate(tr)
        tr.create_sketch("front")
        tr.draw_circle((-30, 0), 8)
        tr.cut_extrude(True, None, False, None)
        tr.create_axis("x")
        tr.linear_pattern(["Cut-Extrude1"], "x", 30, 3, None)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 8)
        with pytest.raises(ModelError, match="already removed"):
            tr.cut_extrude(True, None, False, None)


# ---------------------------------------------------------------------------
# Macro correctness
# ---------------------------------------------------------------------------


class TestReversedBossCornerHoles:
    def test_corner_holes_follow_boss_direction(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.2",
                "commands": [
                    {"op": "new_part"},
                    {"op": "create_sketch"},
                    {"op": "draw_rectangle", "width": 100, "height": 50},
                    {"op": "extrude", "depth": 10, "reverse": True},
                    {"op": "add_corner_holes", "diameter": 8, "margin": 10},
                ],
            }
        )
        expanded = expand_commands(list(cf.commands))
        cuts = [ec.command for ec in expanded if isinstance(ec.command, CutExtrude)]
        assert cuts[-1].reverse is True


class TestHoleDepthVsThickness:
    def test_counterbore_deeper_than_plate_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="only 5.0 mm of"):
            expand_commands(
                list(
                    parse_command_data(
                        {
                            "schema_version": "0.2",
                            "commands": [
                                {"op": "create_plate", "width": 100, "height": 50, "thickness": 5},
                                # M6 preset: cb_depth 6mm > 5mm plate
                                {
                                    "op": "hole",
                                    "type": "counterbore",
                                    "standard": "M6",
                                    "at": [[0, 0]],
                                },
                            ],
                        }
                    ).commands
                )
            )

    def test_countersink_cone_deeper_than_plate_rejected(self) -> None:
        with pytest.raises(CommandFileError, match="countersink cone"):
            expand_commands(
                list(
                    parse_command_data(
                        {
                            "schema_version": "0.2",
                            "commands": [
                                {"op": "create_plate", "width": 100, "height": 50, "thickness": 2},
                                # M6 preset cone depth = 3mm > 2mm plate
                                {
                                    "op": "hole",
                                    "type": "countersink",
                                    "standard": "M6",
                                    "at": [[0, 0]],
                                },
                            ],
                        }
                    ).commands
                )
            )

    def test_fitting_counterbore_accepted(self) -> None:
        expanded = expand_commands(
            list(
                parse_command_data(
                    {
                        "schema_version": "0.2",
                        "commands": [
                            {"op": "create_plate", "width": 100, "height": 50, "thickness": 12},
                            {"op": "hole", "type": "counterbore", "standard": "M6", "at": [[0, 0]]},
                        ],
                    }
                ).commands
            )
        )
        assert execute(expanded, MockBackend()).success


class TestDisjointBossHoleDirection:
    def test_hole_aims_at_nearest_material(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.2",
                "commands": [
                    {"op": "new_part"},
                    {"op": "create_sketch"},
                    {"op": "draw_rectangle", "width": 100, "height": 50},
                    {"op": "extrude", "depth": 10},  # material [0, 10]
                    {"op": "create_plane", "name": "Far", "offset_from": "front", "distance": 40},
                    {"op": "create_sketch", "plane": "Far"},
                    {"op": "draw_rectangle", "width": 100, "height": 50},
                    {"op": "extrude", "depth": 10},  # material [40, 50]
                    {"op": "create_plane", "name": "Mid", "offset_from": "front", "distance": 12},
                    # plane at 12: nearest material is [0, 10] just below,
                    # so the hole must drill DOWN (reverse), not up at [40, 50]
                    {"op": "hole", "at": [[0, 0]], "diameter": 5, "on": "Mid"},
                ],
            }
        )
        expanded = expand_commands(list(cf.commands))
        cuts = [ec.command for ec in expanded if isinstance(ec.command, CutExtrude)]
        assert cuts[-1].reverse is True


# ---------------------------------------------------------------------------
# Schema robustness
# ---------------------------------------------------------------------------


class TestHoleStandardWithNulls:
    def test_null_dimensions_defer_to_standard(self) -> None:
        h = Hole.model_validate(
            {
                "op": "hole",
                "type": "counterbore",
                "standard": "M6",
                "diameter": None,
                "cb_diameter": None,
                "cb_depth": None,
                "at": [[0, 0]],
            }
        )
        assert h.diameter == 6.6 and h.cb_diameter == 11.0 and h.cb_depth == 6.0

    def test_explicit_value_still_wins(self) -> None:
        h = Hole.model_validate(
            {"op": "hole", "standard": "M6", "diameter": 7.0, "at": [[0, 0]]}
        )
        assert h.diameter == 7.0


class TestCreateSketchRoundTrip:
    def test_model_dump_revalidates(self) -> None:
        s = CreateSketch.model_validate({"op": "create_sketch", "on": {"facing": "+z"}})
        again = CreateSketch.model_validate(s.model_dump())
        assert again.on is not None and again.on.facing == "+z"

    def test_non_default_plane_with_on_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            CreateSketch.model_validate(
                {"op": "create_sketch", "plane": "top", "on": {"facing": "+z"}}
            )


# ---------------------------------------------------------------------------
# Executor / CLI reporting
# ---------------------------------------------------------------------------


class TestWarningsBeforeErrorAttributed:
    def test_pre_error_warning_lands_on_failing_command(self) -> None:
        # Two-boss seam: first contour warns (spans footprints), second
        # errors (misses material) — both in one cut_extrude command.
        cf = CommandFile.model_validate(
            {
                "schema_version": "0.2",
                "commands": [
                    {"op": "new_part"},
                    {"op": "create_sketch"},
                    {"op": "draw_rectangle", "width": 100, "height": 50},
                    {"op": "extrude", "depth": 5},
                    {"op": "create_sketch"},
                    {"op": "draw_rectangle", "center": [70, 0], "width": 60, "height": 50},
                    {"op": "extrude", "depth": 5},
                    {"op": "create_sketch"},
                    {"op": "draw_circle", "center": [45, 0], "diameter": 12},
                    {"op": "draw_circle", "center": [0, 100], "diameter": 8},
                    {"op": "cut_extrude"},
                ],
            }
        )
        raw = [
            ExpandedCommand(command=c, source_index=i, source_op=c.op)  # type: ignore[arg-type]
            for i, c in enumerate(cf.commands)
        ]
        report = execute(raw, MockBackend())
        assert report.success is False
        failing = [r for r in report.results if r.status == "error"][0]
        assert any("footprint unions" in w for w in failing.warnings)


class TestReportSchemaVersion:
    def test_execute_accepts_declared_version(self) -> None:
        cf = parse_command_data(
            {"schema_version": "0.1", "commands": [{"op": "new_part"}]}
        )
        report = execute(
            expand_commands(list(cf.commands)), MockBackend(), schema_version=cf.schema_version
        )
        assert report.schema_version == "0.1"
