"""2D geometry predicates on sketch planes (mm).

Shapes: axis-aligned rectangles, circles, and slots (capsules — the
stadium outline SolidWorks' straight sketch slot produces).

SolidWorks-fidelity note: a single sketch used by one extrude/cut may
contain multiple closed contours only if each pair is *strictly* nested
or *strictly* disjoint. Contours that cross OR merely touch (tangent)
produce self-intersecting or zero-thickness geometry, which SolidWorks
rejects — so tangency counts as invalid here too, using the shared EPS.

Strictness conventions:

* :func:`contains` / :func:`disjoint` — strict by EPS (touching fails)
* :func:`covers` — non-strict by EPS (touching/equality passes); used
  for "lies inside already-removed material" checks where an exact
  duplicate must also match
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from swpilot.tolerances import EPS

__all__ = [
    "EPS",
    "Circle",
    "Rect",
    "Shape",
    "Slot",
    "contains",
    "covers",
    "disjoint",
    "valid_contour_pair",
]


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle, center + size."""

    cx: float
    cy: float
    width: float
    height: float

    @property
    def xmin(self) -> float:
        return self.cx - self.width / 2.0

    @property
    def xmax(self) -> float:
        return self.cx + self.width / 2.0

    @property
    def ymin(self) -> float:
        return self.cy - self.height / 2.0

    @property
    def ymax(self) -> float:
        return self.cy + self.height / 2.0


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    diameter: float

    @property
    def r(self) -> float:
        return self.diameter / 2.0


@dataclass(frozen=True)
class Slot:
    """Straight slot (capsule): segment from (x1,y1) to (x2,y2), full width."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float

    @property
    def r(self) -> float:
        return self.width / 2.0

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


Shape = Rect | Circle | Slot


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _rect_corners(r: Rect) -> list[tuple[float, float]]:
    return [(r.xmin, r.ymin), (r.xmax, r.ymin), (r.xmin, r.ymax), (r.xmax, r.ymax)]


def _slot_ends(s: Slot) -> list[tuple[float, float]]:
    return [(s.x1, s.y1), (s.x2, s.y2)]


def _point_segment_dist(px: float, py: float, s: Slot) -> float:
    """Distance from a point to the slot's center segment."""
    dx, dy = s.x2 - s.x1, s.y2 - s.y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return _dist(px, py, s.x1, s.y1)
    t = ((px - s.x1) * dx + (py - s.y1) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    return _dist(px, py, s.x1 + t * dx, s.y1 + t * dy)


def _segments_intersect(a: Slot, b: Slot) -> bool:
    def orient(ox: float, oy: float, px: float, py: float, qx: float, qy: float) -> float:
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = orient(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1)
    d2 = orient(a.x1, a.y1, a.x2, a.y2, b.x2, b.y2)
    d3 = orient(b.x1, b.y1, b.x2, b.y2, a.x1, a.y1)
    d4 = orient(b.x1, b.y1, b.x2, b.y2, a.x2, a.y2)
    return d1 * d2 < 0 and d3 * d4 < 0


def _segment_segment_dist(a: Slot, b: Slot) -> float:
    if _segments_intersect(a, b):
        return 0.0
    return min(
        _point_segment_dist(a.x1, a.y1, b),
        _point_segment_dist(a.x2, a.y2, b),
        _point_segment_dist(b.x1, b.y1, a),
        _point_segment_dist(b.x2, b.y2, a),
    )


def _point_in_rect(px: float, py: float, r: Rect) -> bool:
    return r.xmin <= px <= r.xmax and r.ymin <= py <= r.ymax


def _segment_rect_dist(s: Slot, r: Rect) -> float:
    """Distance from the slot's center segment to the rectangle (0 if touching)."""
    if _point_in_rect(s.x1, s.y1, r) or _point_in_rect(s.x2, s.y2, r):
        return 0.0
    corners = _rect_corners(r)
    edges = [
        Slot(corners[0][0], corners[0][1], corners[1][0], corners[1][1], 0.0),
        Slot(corners[1][0], corners[1][1], corners[3][0], corners[3][1], 0.0),
        Slot(corners[3][0], corners[3][1], corners[2][0], corners[2][1], 0.0),
        Slot(corners[2][0], corners[2][1], corners[0][0], corners[0][1], 0.0),
    ]
    return min(_segment_segment_dist(s, e) for e in edges)


def _rect_circle_dist(r: Rect, c: Circle) -> float:
    """Distance from the circle's *center* to the rectangle (0 if inside)."""
    dx = max(r.xmin - c.cx, 0.0, c.cx - r.xmax)
    dy = max(r.ymin - c.cy, 0.0, c.cy - r.ymax)
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
# contains (strict) / covers (non-strict)
# --------------------------------------------------------------------------


def _inside(outer: Shape, inner: Shape, margin: float) -> bool:
    """``inner`` within ``outer`` with boundary clearance >= ``margin``.

    ``margin`` > 0 gives strict containment; ``margin`` < 0 gives
    non-strict (touching allowed). Convexity of all three shape kinds
    lets circles/capsules be tested via center/segment distances and
    rectangles via their corners.
    """
    if isinstance(inner, Circle):
        if isinstance(outer, Rect):
            return (
                inner.cx - inner.r >= outer.xmin + margin
                and inner.cx + inner.r <= outer.xmax - margin
                and inner.cy - inner.r >= outer.ymin + margin
                and inner.cy + inner.r <= outer.ymax - margin
            )
        if isinstance(outer, Circle):
            d = _dist(outer.cx, outer.cy, inner.cx, inner.cy)
            return d + inner.r <= outer.r - margin
        if isinstance(outer, Slot):
            return _point_segment_dist(inner.cx, inner.cy, outer) + inner.r <= outer.r - margin
    if isinstance(inner, Rect):
        if isinstance(outer, Rect):
            return (
                inner.xmin >= outer.xmin + margin
                and inner.xmax <= outer.xmax - margin
                and inner.ymin >= outer.ymin + margin
                and inner.ymax <= outer.ymax - margin
            )
        if isinstance(outer, Circle):
            return all(
                _dist(outer.cx, outer.cy, px, py) <= outer.r - margin
                for px, py in _rect_corners(inner)
            )
        if isinstance(outer, Slot):
            return all(
                _point_segment_dist(px, py, outer) <= outer.r - margin
                for px, py in _rect_corners(inner)
            )
    if isinstance(inner, Slot):
        # A capsule lies inside a convex shape iff both end disks do.
        return all(
            _inside(outer, Circle(px, py, inner.width), margin) for px, py in _slot_ends(inner)
        )
    raise TypeError(f"unsupported shape pair: {type(outer)}, {type(inner)}")


def contains(outer: Shape, inner: Shape) -> bool:
    """True if ``inner`` lies strictly inside ``outer`` (no touching)."""
    return _inside(outer, inner, EPS)


def covers(outer: Shape, inner: Shape) -> bool:
    """True if ``inner`` lies inside ``outer``, touching/equality allowed."""
    return _inside(outer, inner, -EPS)


# --------------------------------------------------------------------------
# disjoint (strict)
# --------------------------------------------------------------------------


def disjoint(a: Shape, b: Shape) -> bool:
    """True if the shapes are strictly separated (no touching)."""
    if isinstance(a, Circle) and isinstance(b, Circle):
        return _dist(a.cx, a.cy, b.cx, b.cy) > a.r + b.r + EPS
    if isinstance(a, Rect) and isinstance(b, Rect):
        return (
            a.xmax < b.xmin - EPS
            or b.xmax < a.xmin - EPS
            or a.ymax < b.ymin - EPS
            or b.ymax < a.ymin - EPS
        )
    if isinstance(a, Slot) and isinstance(b, Slot):
        return _segment_segment_dist(a, b) > a.r + b.r + EPS
    if isinstance(a, Rect) and isinstance(b, Circle):
        return _rect_circle_dist(a, b) > b.r + EPS
    if isinstance(a, Circle) and isinstance(b, Rect):
        return disjoint(b, a)
    if isinstance(a, Slot) and isinstance(b, Circle):
        return _point_segment_dist(b.cx, b.cy, a) > a.r + b.r + EPS
    if isinstance(a, Circle) and isinstance(b, Slot):
        return disjoint(b, a)
    if isinstance(a, Slot) and isinstance(b, Rect):
        return _segment_rect_dist(a, b) > a.r + EPS
    if isinstance(a, Rect) and isinstance(b, Slot):
        return disjoint(b, a)
    raise TypeError(f"unsupported shape pair: {type(a)}, {type(b)}")


def valid_contour_pair(a: Shape, b: Shape) -> bool:
    """True if two contours may coexist in one feature sketch."""
    return contains(a, b) or contains(b, a) or disjoint(a, b)
