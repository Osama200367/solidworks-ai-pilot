"""Regression tests pinning fixes from the v0.1 adversarial review.

Each test class corresponds to one confirmed finding; if any of these
regress, the failure mode they describe returns.
"""

import pytest
from pydantic import ValidationError

from swpilot.backends import calls
from swpilot.backends.base import BackendError
from swpilot.backends.mock.geometry import Circle, Rect, covers
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import expand_commands, parse_command_data
from swpilot.commands.schema import CutExtrude, DrawCircle
from swpilot.executor import execute


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

    def _two_bosses(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        be.draw_rectangle((0, 0), 100, 50)
        be.extrude(5)
        be.create_sketch("front")
        be.draw_rectangle((70, 0), 60, 50)  # x in [40, 100]: overlaps the first
        be.extrude(5)
        be.pop_warnings()

    def test_seam_spanning_cut_warns_instead_of_failing(self) -> None:
        be = MockBackend()
        self._two_bosses(be)
        be.create_sketch("front")
        be.draw_circle((45, 0), 12)  # strictly inside the union, inside neither rect
        be.cut_extrude(True, None)  # must not raise
        assert any("cannot compute footprint unions" in w for w in be.pop_warnings())

    def test_fully_outside_cut_still_fails(self) -> None:
        be = MockBackend()
        self._two_bosses(be)
        be.create_sketch("front")
        be.draw_circle((0, 100), 8)  # disjoint from all material
        with pytest.raises(BackendError, match="miss the part entirely"):
            be.cut_extrude(True, None)


class TestCutInsideRemovedMaterial:
    """A cut entirely inside a previous through-all hole cannot intersect the model."""

    def _plate_with_hole(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        be.draw_rectangle((0, 0), 100, 50)
        be.extrude(10)
        be.create_sketch("front")
        be.draw_circle((0, 0), 20)
        be.cut_extrude(True, None)

    def test_cut_inside_existing_hole_rejected(self) -> None:
        be = MockBackend()
        self._plate_with_hole(be)
        be.create_sketch("front")
        be.draw_circle((0, 0), 10)  # entirely within the removed d=20 disk
        with pytest.raises(BackendError, match="already removed by Cut-Extrude1"):
            be.cut_extrude(True, None)

    def test_exact_duplicate_hole_rejected(self) -> None:
        be = MockBackend()
        self._plate_with_hole(be)
        be.create_sketch("front")
        be.draw_circle((0, 0), 20)  # identical to the previous hole
        with pytest.raises(BackendError, match="already removed"):
            be.cut_extrude(True, None)

    def test_covers_is_non_strict(self) -> None:
        assert covers(Circle(0, 0, 20), Circle(0, 0, 20))
        assert covers(Rect(0, 0, 10, 10), Rect(0, 0, 10, 10))
        assert not covers(Circle(0, 0, 20), Circle(1, 0, 20))


class TestBooleanDimensionRejection:
    """JSON true/false must not lax-coerce to 1.0/0.0 mm."""

    def test_bool_dimension_rejected(self) -> None:
        with pytest.raises(ValidationError, match="booleans"):
            DrawCircle.model_validate({"op": "draw_circle", "diameter": True})

    def test_bool_coordinate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="booleans"):
            DrawCircle.model_validate({"op": "draw_circle", "center": [True, 0], "diameter": 5})

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


class TestMacroSimulatorConsistency:
    """Any macro-accepted file must pass the simulator's stricter EPS checks."""

    def test_barely_legal_margin_accepted_by_both(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.1",
                "commands": [
                    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
                    # margin - r = 0.001 mm: legal by a hair, must survive simulation
                    {"op": "add_corner_holes", "diameter": 8, "margin": 4.001},
                ],
            }
        )
        report = execute(expand_commands(list(cf.commands)), MockBackend())
        assert report.success, [r.error for r in report.results if r.error]

    def test_margin_within_eps_of_radius_rejected_at_expansion(self) -> None:
        from swpilot.commands.loader import CommandFileError

        with pytest.raises(CommandFileError, match="exceed the hole radius"):
            expand_commands(
                list(
                    parse_command_data(
                        {
                            "schema_version": "0.1",
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


class TestExecutorExceptionContainment:
    """No exception may escape execute(): the report is the error channel."""

    def test_unexpected_exception_recorded_as_error(self) -> None:
        class ExplodingBackend(MockBackend):
            def extrude(self, depth: float) -> None:
                raise KeyError("simulated backend bug")

        cf = parse_command_data(
            {
                "schema_version": "0.1",
                "commands": [{"op": "create_plate", "width": 10, "height": 10, "thickness": 2}],
            }
        )
        report = execute(expand_commands(list(cf.commands)), ExplodingBackend())
        assert report.success is False
        failing = [r for r in report.results if r.status == "error"]
        assert len(failing) == 1
        assert "KeyError" in (failing[0].error or "")

    def test_finalize_failure_recorded_not_raised(self) -> None:
        class FailingFinalize(MockBackend):
            def finalize(self) -> None:
                raise RuntimeError("zoom failed")

        cf = parse_command_data(
            {"schema_version": "0.1", "commands": [{"op": "new_part"}]}
        )
        report = execute(expand_commands(list(cf.commands)), FailingFinalize())
        assert report.success is False
        assert report.finalize_error is not None
        assert "zoom failed" in report.finalize_error
        assert "finalize_error" in report.to_dict()


class TestRelativeSavePathWarning:
    """Mock warns that the COM backend will absolutize relative save paths."""

    def test_relative_path_warns(self) -> None:
        be = MockBackend()
        be.new_part()
        be.save_part("out.SLDPRT")
        assert any("relative" in w for w in be.pop_warnings())

    def test_absolute_path_does_not_warn_about_resolution(self) -> None:
        be = MockBackend()
        be.new_part()
        be.save_part("/abs/out.SLDPRT")
        assert not any("relative" in w for w in be.pop_warnings())
