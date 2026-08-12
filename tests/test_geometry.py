"""Geometry predicate tests for the mock simulator."""

from swpilot.backends.mock.geometry import Circle, Rect, contains, disjoint, valid_contour_pair

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
