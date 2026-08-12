"""2D geometry predicates for simulator validation.

Shapes live on a sketch plane, dimensions in millimeters.

SolidWorks-fidelity note: a single sketch used by one extrude/cut may
contain multiple closed contours only if each pair is *strictly* nested
or *strictly* disjoint. Contours that cross OR merely touch (tangent)
produce self-intersecting or zero-thickness geometry, which SolidWorks
rejects — so tangency counts as invalid here too, using a strict EPS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 1e-6 mm = 1 nm: far below manufacturing relevance, far above float noise
# at the coordinate magnitudes we deal with (~1e2 mm).
EPS = 1e-6


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


Shape = Rect | Circle


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _rect_corners(r: Rect) -> list[tuple[float, float]]:
    return [(r.xmin, r.ymin), (r.xmax, r.ymin), (r.xmin, r.ymax), (r.xmax, r.ymax)]


def contains(outer: Shape, inner: Shape) -> bool:
    """True if ``inner`` lies strictly inside ``outer`` (no touching)."""
    if isinstance(outer, Rect) and isinstance(inner, Circle):
        return (
            inner.cx - inner.r > outer.xmin + EPS
            and inner.cx + inner.r < outer.xmax - EPS
            and inner.cy - inner.r > outer.ymin + EPS
            and inner.cy + inner.r < outer.ymax - EPS
        )
    if isinstance(outer, Circle) and isinstance(inner, Circle):
        d = _dist(outer.cx, outer.cy, inner.cx, inner.cy)
        return d + inner.r < outer.r - EPS
    if isinstance(outer, Rect) and isinstance(inner, Rect):
        return (
            inner.xmin > outer.xmin + EPS
            and inner.xmax < outer.xmax - EPS
            and inner.ymin > outer.ymin + EPS
            and inner.ymax < outer.ymax - EPS
        )
    if isinstance(outer, Circle) and isinstance(inner, Rect):
        return all(
            _dist(outer.cx, outer.cy, px, py) < outer.r - EPS for px, py in _rect_corners(inner)
        )
    raise TypeError(f"unsupported shape pair: {type(outer)}, {type(inner)}")


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
    if isinstance(a, Rect) and isinstance(b, Circle):
        rect, circle = a, b
    elif isinstance(a, Circle) and isinstance(b, Rect):
        rect, circle = b, a
    else:
        raise TypeError(f"unsupported shape pair: {type(a)}, {type(b)}")
    # Distance from circle center to the rectangle (0 if the center is inside).
    dx = max(rect.xmin - circle.cx, 0.0, circle.cx - rect.xmax)
    dy = max(rect.ymin - circle.cy, 0.0, circle.cy - rect.ymax)
    return math.hypot(dx, dy) > circle.r + EPS


def valid_contour_pair(a: Shape, b: Shape) -> bool:
    """True if two contours may coexist in one feature sketch."""
    return contains(a, b) or contains(b, a) or disjoint(a, b)
