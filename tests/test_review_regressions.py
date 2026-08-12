"""Regression tests pinning fixes from the v0.1 adversarial review.

Each test class corresponds to one confirmed finding; if any of these
regress, the failure mode they describe returns. (v0.2 moved validation
from the mock backend into the shared ModelTracker; the scenarios and
guarantees are unchanged.)
"""

import pytest
from pydantic import ValidationError

from swpilot.backends import calls
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import CommandFileError, expand_commands, parse_command_data
from swpilot.commands.schema import CutExtrude, DrawCircle
from swpilot.executor import execute
from swpilot.model.geometry import Circle, Rect, covers
from swpilot.model.tracker import ModelError, ModelTracker


class TestSaveAs3StatusSemantics:
    """SaveAs3 returns a Long status where 0 = success; 'truthy' would invert it."""

    def test_save_spec_uses_status_zero_check(self) -> None:
        (spec,) = calls.save_part_calls("x.SLDPRT")
        assert spec.method == "SaveAs3"
        assert spec.check == "status_zero"

    def test_template_spec_has_no_truthy_check(self) -> None:
        # An unset template returns "" legitimately; new_part raises the
        # tailored guidance error instead of a generic truthy failure.
        assert calls.get_default_part_template().check == "none"


class TestMergedBossSeamCut:
    """A cut spanning two merged same-plane bosses must warn, not hard-fail."""

    def _two_bosses(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_rectangle((0, 0), 100, 50)
        tr.extrude(5, False)
        tr.create_sketch("front")
        tr.draw_rectangle((70, 0), 60, 50)  # x in [40, 100]: overlaps the first
        tr.extrude(5, False)
        tr.pop_warnings()

    def test_seam_spanning_cut_warns_instead_of_failing(self) -> None:
        tr = ModelTracker()
        self._two_bosses(tr)
        tr.create_sketch("front")
        tr.draw_circle((45, 0), 12)  # strictly inside the union, inside neither rect
        tr.cut_extrude(True, None, False, None)  # must not raise
        assert any("footprint unions" in w for w in tr.pop_warnings())

    def test_fully_outside_cut_still_fails(self) -> None:
        tr = ModelTracker()
        self._two_bosses(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 100), 8)  # disjoint from all material
        with pytest.raises(ModelError, match="miss the part entirely"):
            tr.cut_extrude(True, None, False, None)


class TestCutInsideRemovedMaterial:
    """A cut entirely inside a previous through-all hole cannot intersect the model."""

    def _plate_with_hole(self, tr: ModelTracker) -> None:
        tr.new_part()
        tr.create_sketch("front")
        tr.draw_rectangle((0, 0), 100, 50)
        tr.extrude(10, False)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 20)
        tr.cut_extrude(True, None, False, None)

    def test_cut_inside_existing_hole_rejected(self) -> None:
        tr = ModelTracker()
        self._plate_with_hole(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 10)  # entirely within the removed d=20 disk
        with pytest.raises(ModelError, match="already removed by Cut-Extrude1"):
            tr.cut_extrude(True, None, False, None)

    def test_exact_duplicate_hole_rejected(self) -> None:
        tr = ModelTracker()
        self._plate_with_hole(tr)
        tr.create_sketch("front")
        tr.draw_circle((0, 0), 20)  # identical to the previous hole
        with pytest.raises(ModelError, match="already removed"):
            tr.cut_extrude(True, None, False, None)

    def test_covers_is_non_strict(self) -> None:
        assert covers(Circle(0, 0, 20), Circle(0, 0, 20))
        assert covers(Rect(0, 0, 10, 10), Rect(0, 0, 10, 10))
        assert not covers(Circle(0, 0, 20), Circle(1, 0, 20))


class TestBooleanDimensionRejection:
    """JSON true/false must not lax-coerce to 1.0/0.0 mm."""

    def test_bool_dimension_rejected(self) -> None:
        with pytest.raises(ValidationError, match="booleans"):
            DrawCircle.model_validate({"op": "draw_circle", "diameter": True})

    def test_int_dimensions_still_accepted(self) -> None:
        c = DrawCircle.model_validate({"op": "draw_circle", "diameter": 8})
        assert c.diameter == 8.0


class TestCutExtrudeNullDepth:
    """Explicit "depth": null must mean the same as omitting depth."""

    def test_null_depth_means_through_all(self) -> None:
        c = CutExtrude.model_validate({"op": "cut_extrude", "depth": None})
        assert c.through_all is True

    def test_null_depth_with_blind_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires 'depth'"):
            CutExtrude.model_validate({"op": "cut_extrude", "through_all": False, "depth": None})


class TestMacroTrackerConsistency:
    """Any macro-accepted file must pass the tracker's stricter EPS checks."""

    def test_barely_legal_margin_accepted_by_both(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.2",
                "commands": [
                    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
                    # margin - r = 0.001 mm: legal by a hair, must survive execution
                    {"op": "add_corner_holes", "diameter": 8, "margin": 4.001},
                ],
            }
        )
        report = execute(expand_commands(list(cf.commands)), MockBackend())
        assert report.success, [r.error for r in report.results if r.error]

    def test_margin_within_eps_of_radius_rejected_at_expansion(self) -> None:
        with pytest.raises(CommandFileError, match="exceed the hole radius"):
            expand_commands(
                list(
                    parse_command_data(
                        {
                            "schema_version": "0.2",
                            "commands": [
                                {
                                    "op": "create_plate",
                                    "width": 100,
                                    "height": 50,
                                    "thickness": 10,
                                },
                                {
                                    "op": "add_corner_holes",
                                    "diameter": 8,
                                    "margin": 4.0000000001,
                                },
                            ],
                        }
                    ).commands
                )
            )


class TestRelativeSavePathWarning:
    """The twin warns that the COM backend will absolutize relative save paths."""

    def test_relative_path_warns(self) -> None:
        tr = ModelTracker()
        tr.new_part()
        tr.save_part("out.SLDPRT")
        assert any("relative" in w for w in tr.pop_warnings())
