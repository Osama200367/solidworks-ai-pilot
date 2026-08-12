"""Geometry predicate tests for the shared model layer."""

from swpilot.model.geometry import (
    Circle,
    Rect,
    Slot,
    contains,
    covers,
    disjoint,
    valid_contour_pair,
)

PLATE = Rect(0, 0, 100, 50)


class TestContains:
    def test_circle_strictly_inside_rect(self) -> None:
        assert contains(PLATE, Circle(40, 15, 8))

    def test_circle_touching_rect_edge_not_contained(self) -> None:
        # circle edge exactly on x = 50
        assert not contains(PLATE, Circle(46, 0, 8))

    def test_circle_outside_rect(self) -> None:
        assert not contains(PLATE, Circle(60, 0, 8))

    def test_circle_in_circle(self) -> None:
        assert contains(Circle(0, 0, 20), Circle(2, 0, 10))
        assert not contains(Circle(0, 0, 20), Circle(5, 0, 10))  # internally tangent
        assert not contains(Circle(0, 0, 20), Circle(0, 0, 20))  # identical

    def test_rect_in_rect(self) -> None:
        assert contains(PLATE, Rect(0, 0, 90, 40))
        assert not contains(PLATE, Rect(0, 0, 100, 40))  # shared edges

    def test_rect_in_circle(self) -> None:
        assert contains(Circle(0, 0, 40), Rect(0, 0, 10, 10))
        assert not contains(Circle(0, 0, 40), Rect(0, 0, 40, 40))

    def test_slot_in_rect(self) -> None:
        assert contains(PLATE, Slot(-20, 0, 20, 0, 10))
        assert not contains(PLATE, Slot(-50, 0, 50, 0, 10))  # end caps poke out

    def test_circle_in_slot(self) -> None:
        slot = Slot(-20, 0, 20, 0, 10)
        assert contains(slot, Circle(0, 0, 4))
        assert not contains(slot, Circle(0, 0, 10))  # same width: tangent
        assert not contains(slot, Circle(0, 4, 4))  # crosses the wall

    def test_slot_in_circle(self) -> None:
        assert contains(Circle(0, 0, 60), Slot(-10, 0, 10, 0, 8))
        assert not contains(Circle(0, 0, 20), Slot(-10, 0, 10, 0, 8))


class TestCovers:
    def test_covers_is_non_strict(self) -> None:
        assert covers(Circle(0, 0, 20), Circle(0, 0, 20))
        assert covers(Rect(0, 0, 10, 10), Rect(0, 0, 10, 10))
        assert covers(Slot(-10, 0, 10, 0, 8), Slot(-10, 0, 10, 0, 8))
        assert not covers(Circle(0, 0, 20), Circle(1, 0, 20))

    def test_covers_inscribed_circle_in_rect(self) -> None:
        assert covers(Rect(0, 0, 10, 10), Circle(0, 0, 10))
        assert not contains(Rect(0, 0, 10, 10), Circle(0, 0, 10))


class TestDisjoint:
    def test_separated_circles(self) -> None:
        assert disjoint(Circle(0, 0, 10), Circle(20, 0, 10))

    def test_tangent_circles_not_disjoint(self) -> None:
        assert not disjoint(Circle(0, 0, 10), Circle(10, 0, 10))

    def test_overlapping_circles_not_disjoint(self) -> None:
        assert not disjoint(Circle(0, 0, 10), Circle(5, 0, 10))

    def test_rects(self) -> None:
        assert disjoint(Rect(0, 0, 10, 10), Rect(20, 0, 10, 10))
        assert not disjoint(Rect(0, 0, 10, 10), Rect(10, 0, 10, 10))  # touching edges

    def test_rect_circle(self) -> None:
        assert disjoint(Rect(0, 0, 10, 10), Circle(20, 0, 10))
        assert not disjoint(Rect(0, 0, 10, 10), Circle(10, 0, 10))  # tangent to edge
        assert not disjoint(Rect(0, 0, 10, 10), Circle(0, 0, 4))  # circle inside rect

    def test_symmetry(self) -> None:
        assert disjoint(Circle(20, 0, 10), Rect(0, 0, 10, 10))

    def test_slot_circle(self) -> None:
        slot = Slot(-20, 0, 20, 0, 10)
        assert disjoint(slot, Circle(0, 20, 8))
        assert not disjoint(slot, Circle(0, 9, 8))  # tangent to slot wall
        assert not disjoint(slot, Circle(25, 0, 12))  # overlaps the end cap

    def test_slot_rect(self) -> None:
        slot = Slot(-20, 0, 20, 0, 10)
        assert disjoint(slot, Rect(0, 30, 10, 10))
        assert not disjoint(slot, Rect(0, 8, 10, 10))

    def test_slot_slot(self) -> None:
        assert disjoint(Slot(-10, 0, 10, 0, 6), Slot(-10, 20, 10, 20, 6))
        assert not disjoint(Slot(-10, 0, 10, 0, 6), Slot(0, -5, 0, 5, 6))  # crossing


class TestValidContourPair:
    def test_nested_is_valid(self) -> None:
        # plate outline with a hole in one sketch: legal in SolidWorks
        assert valid_contour_pair(PLATE, Circle(0, 0, 10))

    def test_disjoint_is_valid(self) -> None:
        assert valid_contour_pair(Circle(-40, -15, 8), Circle(40, 15, 8))

    def test_crossing_is_invalid(self) -> None:
        assert not valid_contour_pair(Circle(0, 0, 10), Circle(5, 0, 10))

    def test_tangent_is_invalid(self) -> None:
        # zero-thickness geometry
        assert not valid_contour_pair(Circle(0, 0, 10), Circle(10, 0, 10))

    def test_slot_beside_circles_valid(self) -> None:
        assert valid_contour_pair(Slot(-20, 0, 20, 0, 10), Circle(0, 20, 8))
