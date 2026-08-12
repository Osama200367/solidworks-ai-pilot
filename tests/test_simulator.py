"""Mock backend (stateful simulator) tests."""

import pytest

from swpilot.backends.base import BackendError
from swpilot.backends.mock.simulator import MockBackend


@pytest.fixture
def be() -> MockBackend:
    return MockBackend()


def make_plate(be: MockBackend) -> None:
    be.new_part()
    be.create_sketch("front")
    be.draw_rectangle((0, 0), 100, 50)
    be.extrude(10)


class TestLifecycleRules:
    def test_draw_without_part(self, be: MockBackend) -> None:
        with pytest.raises(BackendError, match="no part is open"):
            be.create_sketch("front")

    def test_draw_without_sketch(self, be: MockBackend) -> None:
        be.new_part()
        with pytest.raises(BackendError, match="no active sketch"):
            be.draw_circle((0, 0), 5)

    def test_second_new_part_rejected(self, be: MockBackend) -> None:
        be.new_part()
        with pytest.raises(BackendError, match="already open"):
            be.new_part()

    def test_extrude_empty_sketch_rejected(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        with pytest.raises(BackendError, match="empty"):
            be.extrude(10)

    def test_second_sketch_while_active_rejected(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        with pytest.raises(BackendError, match="still active"):
            be.create_sketch("top")

    def test_cut_without_solid_rejected(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        be.draw_circle((0, 0), 5)
        with pytest.raises(BackendError, match="no solid material"):
            be.cut_extrude(True, None)


class TestContourValidation:
    def test_overlapping_circles_in_one_sketch_rejected(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((0, 0), 10)
        with pytest.raises(BackendError, match="overlaps or touches"):
            be.draw_circle((5, 0), 10)

    def test_nested_contours_allowed(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        be.draw_rectangle((0, 0), 100, 50)
        be.draw_circle((0, 0), 10)  # a hole contour inside the outline: fine
        be.extrude(10)


class TestCutContainment:
    def test_hole_inside_plate_ok(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((40, 15), 8)
        be.cut_extrude(True, None)
        assert [f.name for f in be.model.features] == ["Boss-Extrude1", "Cut-Extrude1"]

    def test_hole_outside_plate_rejected(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((60, 0), 8)
        with pytest.raises(BackendError, match="not strictly inside"):
            be.cut_extrude(True, None)

    def test_hole_crossing_plate_edge_rejected(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((50, 0), 8)  # straddles x=50 edge
        with pytest.raises(BackendError, match="not strictly inside"):
            be.cut_extrude(True, None)

    def test_hole_tangent_to_edge_rejected(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((46, 0), 8)  # tangent to x=50 edge: zero-thickness
        with pytest.raises(BackendError, match="not strictly inside"):
            be.cut_extrude(True, None)

    def test_cut_on_other_plane_warns_instead_of_validating(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("top")
        be.draw_circle((0, 0), 8)
        be.cut_extrude(True, None)
        warnings = be.pop_warnings()
        assert any("cross-plane" in w for w in warnings)


class TestStateAndNaming:
    def test_feature_and_sketch_naming(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((40, 15), 8)
        be.cut_extrude(True, None)
        summary = be.state_summary()
        assert [s["name"] for s in summary["sketches"]] == ["Sketch1", "Sketch2"]
        assert [f["name"] for f in summary["features"]] == ["Boss-Extrude1", "Cut-Extrude1"]
        assert summary["sketches"][0]["consumed_by"] == "Boss-Extrude1"

    def test_save_records_path(self, be: MockBackend) -> None:
        make_plate(be)
        be.save_part("out.SLDPRT")
        assert be.state_summary()["saved_to"] == ["out.SLDPRT"]

    def test_save_without_solid_warns(self, be: MockBackend) -> None:
        be.new_part()
        be.save_part("empty.SLDPRT")
        assert any("no solid geometry" in w for w in be.pop_warnings())

    def test_unconsumed_sketch_warns_at_finalize(self, be: MockBackend) -> None:
        make_plate(be)
        be.create_sketch("front")
        be.draw_circle((0, 0), 5)
        be.finalize()
        assert any("unconsumed sketch" in w for w in be.pop_warnings())


class TestCallLogUnits:
    def test_rectangle_logged_in_meters(self, be: MockBackend) -> None:
        be.new_part()
        be.create_sketch("front")
        be.draw_rectangle((0, 0), 100, 50)
        rect_calls = [c for c in be.call_log if c.method == "CreateCenterRectangle"]
        assert len(rect_calls) == 1
        # corner at (50mm, 25mm) -> (0.05, 0.025) m
        assert rect_calls[0].args == (0.0, 0.0, 0.0, 0.05, 0.025, 0.0)

    def test_extrude_depth_in_meters(self, be: MockBackend) -> None:
        make_plate(be)
        ext = [c for c in be.call_log if c.method == "FeatureExtrusion2"]
        assert len(ext) == 1
        assert ext[0].args[5] == pytest.approx(0.010)  # D1 = 10mm
