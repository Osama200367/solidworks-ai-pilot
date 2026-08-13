"""The digital twin: stateful model tracking, validation, and selection.

The executor maintains one :class:`ModelTracker` per run, for every
backend. It owns everything v0.1's mock simulator validated, plus the
v0.2 3D layer: plane/axis registries, per-feature edge derivation with
world-space pick points, declarative edge-selector resolution, and
material-extent bookkeeping along each plane family's normal.

Geometry is deliberately bounded: all v0.2 solids are axis-aligned
extrusions from standard or offset planes, so faces and edges have
closed-form positions. Approximations are always surfaced as warnings,
never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swpilot.model import geometry as g
from swpilot.model.planes import (
    AXIS_VECTORS,
    AxisName,
    PlaneFamily,
    PlaneFrame,
    Vec3,
    dot,
    offset_frame,
    standard_frame,
)
from swpilot.tolerances import EPS

PLANE_DISPLAY_NAMES: dict[str, str] = {
    "front": "Front Plane",
    "top": "Top Plane",
    "right": "Right Plane",
}

AXIS_FEATURE_NAMES: dict[AxisName, str] = {
    "x": "SWPilot_Axis_X",
    "y": "SWPilot_Axis_Y",
    "z": "SWPilot_Axis_Z",
}

EDGE_GROUPS = ("vertical_corners", "top_loop", "bottom_loop")


class ModelError(ValueError):
    """A command is invalid against the tracked model state."""


@dataclass
class EdgeRec:
    """One selectable edge with its world-space pick point.

    ``max_fillet`` is the per-edge hard bound for a fillet/chamfer applied
    to this edge ALONE; when several edges are selected together the
    tracker applies additional pair rules (opposing cap loops share the
    lateral faces; corner fillets of one rectangle share its sides).
    ``depth_span`` is the extrusion/cut span the edge belongs to (for the
    opposing-loops pair rule); ``entity`` indexes the sketch contour.
    """

    id: str
    feature: str
    group: str  # one of EDGE_GROUPS
    midpoint: Vec3  # mm, world space
    length_mm: float | None
    max_fillet: float | None
    entity: int = 0
    depth_span: float | None = None
    consumed_by: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "feature": self.feature,
            "group": self.group,
            "midpoint": list(self.midpoint),
        }


@dataclass
class SketchRec:
    name: str
    frame: PlaneFrame
    entities: list[g.Shape] = field(default_factory=list)
    consumed_by: str | None = None


@dataclass
class FeatureRec:
    name: str
    kind: str  # boss | cut | fillet | chamfer | linear_pattern | circular_pattern
    sketch: SketchRec | None = None
    depth_mm: float | None = None
    through_all: bool = False
    reverse: bool = False
    draft_angle: float | None = None
    edges: list[EdgeRec] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)
    # Pattern-instance footprints (per plane family), kept out of `detail`
    # so they stay typed and out of report summaries.
    instance_boss_footprints: list[tuple[PlaneFamily, g.Shape]] = field(default_factory=list)
    instance_cut_footprints: list[tuple[PlaneFamily, g.Shape]] = field(default_factory=list)

    @property
    def direction_sign(self) -> float:
        return -1.0 if self.reverse else 1.0


class ModelTracker:
    def __init__(self) -> None:
        self.part_open = False
        self.planes: dict[str, PlaneFrame] = {}
        self.axes: set[AxisName] = set()
        self.sketches: list[SketchRec] = []
        self.features: list[FeatureRec] = []
        self.active_sketch: SketchRec | None = None
        self.saved_to: list[str] = []
        self._warnings: list[str] = []
        self._sketch_n = 0
        self._counters: dict[str, int] = {}
        self._plane_n = 0

    # -- warnings ------------------------------------------------------

    def _warn(self, message: str) -> None:
        self._warnings.append(message)

    def pop_warnings(self) -> list[str]:
        out, self._warnings = self._warnings, []
        return out

    # -- naming --------------------------------------------------------

    def _next_name(self, kind: str) -> str:
        prefix = {
            "boss": "Boss-Extrude",
            "cut": "Cut-Extrude",
            "fillet": "Fillet",
            "chamfer": "Chamfer",
            "linear_pattern": "LPattern",
            "circular_pattern": "CirPattern",
        }[kind]
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{prefix}{self._counters[kind]}"

    def next_auto_plane_name(self) -> str:
        self._plane_n += 1
        return f"SWPilot_Plane{self._plane_n}"

    # -- lookups -------------------------------------------------------

    def _require_part(self, op: str) -> None:
        if not self.part_open:
            raise ModelError(f"{op}: no part is open; start with new_part (or create_plate)")

    def _require_active_sketch(self, op: str) -> SketchRec:
        self._require_part(op)
        if self.active_sketch is None:
            raise ModelError(f"{op}: no active sketch; use create_sketch first")
        return self.active_sketch

    def frame(self, plane_name: str) -> PlaneFrame:
        try:
            return self.planes[plane_name]
        except KeyError:
            raise ModelError(
                f"unknown plane {plane_name!r}; available: {sorted(self.planes)}"
            ) from None

    def plane_display_name(self, plane_name: str) -> str:
        self.frame(plane_name)
        return PLANE_DISPLAY_NAMES.get(plane_name, plane_name)

    def find_plane_at(self, family: PlaneFamily, offset: float) -> str | None:
        for name, frame in self.planes.items():
            if frame.family == family and abs(frame.offset - offset) <= EPS:
                return name
        return None

    def feature(self, name: str) -> FeatureRec:
        for f in self.features:
            if f.name == name:
                return f
        raise ModelError(
            f"unknown feature {name!r}; existing: {[f.name for f in self.features]}"
        )

    def last_boss(self) -> FeatureRec | None:
        for f in reversed(self.features):
            if f.kind == "boss":
                return f
        return None

    @property
    def has_solid(self) -> bool:
        return any(f.kind == "boss" for f in self.features)

    # -- material bookkeeping ------------------------------------------

    def material_interval(self, family: PlaneFamily) -> tuple[float, float] | None:
        """(min, max) extent of boss material along the family normal, mm."""
        lo: float | None = None
        hi: float | None = None
        for f in self.features:
            if f.kind != "boss" or f.sketch is None or f.sketch.frame.family != family:
                continue
            o = f.sketch.frame.offset
            depth = f.depth_mm or 0.0
            a, b = sorted((o, o + f.direction_sign * depth))
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
        if lo is None or hi is None:
            return None
        return lo, hi

    def material_intervals(self, family: PlaneFamily) -> list[tuple[float, float]]:
        """Per-boss (min, max) extents along the family normal, unmerged.

        Unlike :meth:`material_interval`, gaps between disjoint bosses are
        visible here — direction heuristics must aim at real material, not
        at the envelope's midpoint.
        """
        out: list[tuple[float, float]] = []
        for f in self.features:
            if f.kind != "boss" or f.sketch is None or f.sketch.frame.family != family:
                continue
            o = f.sketch.frame.offset
            depth = f.depth_mm or 0.0
            a, b = sorted((o, o + f.direction_sign * depth))
            out.append((a, b))
        return out

    def _boss_footprints(self, family: PlaneFamily) -> list[g.Shape]:
        out = [
            shape
            for f in self.features
            if f.kind == "boss" and f.sketch is not None and f.sketch.frame.family == family
            for shape in f.sketch.entities
        ]
        for f in self.features:
            out.extend(s for fam, s in f.instance_boss_footprints if fam == family)
        return out

    def _removed_footprints(self, family: PlaneFamily) -> list[tuple[str, g.Shape]]:
        out = [
            (f.name, shape)
            for f in self.features
            if f.kind == "cut"
            and f.through_all
            and f.sketch is not None
            and f.sketch.frame.family == family
            for shape in f.sketch.entities
        ]
        for f in self.features:
            out.extend((f.name, s) for fam, s in f.instance_cut_footprints if fam == family)
        return out

    def feature_aabb(self, name: str) -> tuple[Vec3, Vec3]:
        """World axis-aligned bounding box of a boss/cut feature, mm."""
        f = self.feature(name)
        if f.sketch is None or f.kind not in ("boss", "cut"):
            raise ModelError(f"feature {name!r} ({f.kind}) has no boundable geometry")
        frame = f.sketch.frame
        umin = vmin = float("inf")
        umax = vmax = float("-inf")
        for s in f.sketch.entities:
            if isinstance(s, g.Rect):
                bu = (s.xmin, s.xmax)
                bv = (s.ymin, s.ymax)
            elif isinstance(s, g.Circle):
                bu = (s.cx - s.r, s.cx + s.r)
                bv = (s.cy - s.r, s.cy + s.r)
            else:
                bu = (min(s.x1, s.x2) - s.r, max(s.x1, s.x2) + s.r)
                bv = (min(s.y1, s.y2) - s.r, max(s.y1, s.y2) + s.r)
            umin, umax = min(umin, bu[0]), max(umax, bu[1])
            vmin, vmax = min(vmin, bv[0]), max(vmax, bv[1])
        depth = f.depth_mm or 0.0
        n0, n1 = sorted((0.0, f.direction_sign * depth))
        corners = [
            frame.to_world(uu, vv, nn)
            for uu in (umin, umax)
            for vv in (vmin, vmax)
            for nn in (n0, n1)
        ]
        mins = tuple(min(c[i] for c in corners) for i in range(3))
        maxs = tuple(max(c[i] for c in corners) for i in range(3))
        return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])

    # -- operations ----------------------------------------------------

    def new_part(self) -> None:
        if self.part_open:
            raise ModelError("new_part: a part is already open; one part per run")
        self.part_open = True
        for fam in ("front", "top", "right"):
            self.planes[fam] = standard_frame(fam)

    def create_plane(self, name: str, offset_from: str, distance: float) -> PlaneFrame:
        self._require_part("create_plane")
        base = self.frame(offset_from)
        if name in self.planes:
            raise ModelError(f"create_plane: plane name {name!r} already exists")
        new = offset_frame(base, name, distance)
        existing = self.find_plane_at(new.family, new.offset)
        if existing is not None:
            self._warn(
                f"create_plane: {name!r} coincides with existing plane {existing!r}"
            )
        self.planes[name] = new
        return new

    def create_axis(self, axis: AxisName) -> str:
        self._require_part("create_axis")
        if axis in self.axes:
            raise ModelError(
                f"create_axis: axis {axis!r} already exists as {AXIS_FEATURE_NAMES[axis]!r}"
            )
        self.axes.add(axis)
        return AXIS_FEATURE_NAMES[axis]

    def create_sketch(self, plane_name: str) -> PlaneFrame:
        self._require_part("create_sketch")
        if self.active_sketch is not None:
            raise ModelError(
                f"create_sketch: sketch {self.active_sketch.name} is still active; "
                "consume it with extrude/cut_extrude before opening another sketch"
            )
        frame = self.frame(plane_name)
        self._sketch_n += 1
        sketch = SketchRec(name=f"Sketch{self._sketch_n}", frame=frame)
        self.sketches.append(sketch)
        self.active_sketch = sketch
        return frame

    def _add_entity(self, shape: g.Shape, op: str) -> None:
        sketch = self._require_active_sketch(op)
        for existing in sketch.entities:
            if not g.valid_contour_pair(existing, shape):
                raise ModelError(
                    f"{op}: new contour overlaps or touches an existing contour in "
                    f"{sketch.name}; SolidWorks rejects intersecting/tangent contours "
                    "in a single feature sketch (self-intersecting or zero-thickness "
                    "geometry)"
                )
        sketch.entities.append(shape)

    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None:
        self._add_entity(g.Rect(center[0], center[1], width, height), "draw_rectangle")

    def draw_circle(self, center: tuple[float, float], diameter: float) -> None:
        self._add_entity(g.Circle(center[0], center[1], diameter), "draw_circle")

    def draw_slot(
        self, start: tuple[float, float], end: tuple[float, float], width: float
    ) -> None:
        slot = g.Slot(start[0], start[1], end[0], end[1], width)
        if slot.length <= EPS:
            raise ModelError("draw_slot: start and end coincide; use draw_circle instead")
        self._add_entity(slot, "draw_slot")

    def _consume_sketch(self, op: str) -> SketchRec:
        sketch = self._require_active_sketch(op)
        if not sketch.entities:
            raise ModelError(f"{op}: active sketch {sketch.name} is empty; draw something first")
        self.active_sketch = None
        return sketch

    def extrude(self, depth: float, reverse: bool) -> FeatureRec:
        sketch = self._consume_sketch("extrude")
        feature = FeatureRec(
            name=self._next_name("boss"),
            kind="boss",
            sketch=sketch,
            depth_mm=depth,
            reverse=reverse,
        )
        sketch.consumed_by = feature.name
        feature.edges = self._derive_boss_edges(feature)
        self.features.append(feature)
        return feature

    def cut_extrude(
        self,
        through_all: bool,
        depth: float | None,
        reverse: bool,
        draft_angle: float | None,
    ) -> FeatureRec:
        sketch = self._require_active_sketch("cut_extrude")
        if not self.has_solid:
            raise ModelError(
                "cut_extrude: there is no solid material to cut; extrude a base feature first"
            )
        if not sketch.entities:
            raise ModelError(
                f"cut_extrude: active sketch {sketch.name} is empty; draw something first"
            )
        self._validate_cut(sketch, through_all, depth, reverse)
        self.active_sketch = None
        feature = FeatureRec(
            name=self._next_name("cut"),
            kind="cut",
            sketch=sketch,
            depth_mm=depth,
            through_all=through_all,
            reverse=reverse,
            draft_angle=draft_angle,
        )
        sketch.consumed_by = feature.name
        feature.edges = self._derive_cut_edges(feature)
        self.features.append(feature)
        return feature

    def _validate_cut(
        self, sketch: SketchRec, through_all: bool, depth: float | None, reverse: bool
    ) -> None:
        family = sketch.frame.family
        footprints = self._boss_footprints(family)
        if not footprints:
            self._warn(
                f"cut_extrude: no boss feature was sketched on a plane parallel to "
                f"'{family}', so the simulator cannot validate that the cut lands "
                "inside material (cross-family containment is not checked)"
            )
            return
        self._validate_cut_span(sketch, through_all, depth, reverse)
        removed = self._removed_footprints(family)
        for shape in sketch.entities:
            for cut_name, prev in removed:
                if g.covers(prev, shape):
                    raise ModelError(
                        f"cut_extrude: contour {_describe(shape)} in {sketch.name} lies "
                        f"entirely inside material already removed by {cut_name}; the "
                        "cut would not intersect the model"
                    )
            if any(g.contains(outer, shape) for outer in footprints):
                continue
            touching = [outer for outer in footprints if not g.disjoint(outer, shape)]
            if not touching:
                raise ModelError(
                    f"cut_extrude: contour {_describe(shape)} in {sketch.name} does not "
                    "intersect any material footprint on this plane family; the cut "
                    "would miss the part entirely"
                )
            if len(touching) == 1:
                raise ModelError(
                    f"cut_extrude: contour {_describe(shape)} in {sketch.name} is not "
                    "strictly inside the existing material footprint; the cut would "
                    "cross or touch a material edge (zero-thickness geometry)"
                )
            self._warn(
                f"cut_extrude: contour {_describe(shape)} in {sketch.name} spans "
                "several material footprints and is strictly inside none; if it stays "
                "within the merged material this is fine, but if it crosses the "
                "material edge SolidWorks will reject it (footprint unions are not "
                "computed)"
            )

    def _validate_cut_span(
        self, sketch: SketchRec, through_all: bool, depth: float | None, reverse: bool
    ) -> None:
        """The cut's travel along the plane normal must intersect material.

        Footprint containment alone would accept e.g. a through-all cut
        sketched on the top face pointing UP, away from the plate — which
        SolidWorks rejects and which must never be recorded as removed
        material.
        """
        interval = self.material_interval(sketch.frame.family)
        if interval is None:
            return
        lo, hi = interval
        o = sketch.frame.offset
        s = -1.0 if reverse else 1.0
        if through_all:
            span_lo, span_hi = (o, float("inf")) if s > 0 else (float("-inf"), o)
        else:
            span_lo, span_hi = sorted((o, o + s * (depth or 0.0)))
        overlap = min(span_hi, hi) - max(span_lo, lo)
        if overlap <= EPS:
            direction = "+normal" if s > 0 else "-normal"
            raise ModelError(
                f"cut_extrude: the cut travels along {direction} from its sketch "
                f"plane at {o} mm but the material spans [{lo}, {hi}] mm on this "
                "plane family, so the cut cannot intersect any material; flip "
                "'reverse' or move the sketch plane"
            )
        if not through_all and (span_lo < lo - EPS or span_hi > hi + EPS):
            self._warn(
                f"cut_extrude: the blind cut span [{span_lo}, {span_hi}] mm extends "
                f"beyond the material [{lo}, {hi}] mm; SolidWorks will cut only "
                "where material exists"
            )

    # -- edge derivation ----------------------------------------------

    def _derive_boss_edges(self, f: FeatureRec) -> list[EdgeRec]:
        assert f.sketch is not None and f.depth_mm is not None
        frame = f.sketch.frame
        depth = f.depth_mm
        s = f.direction_sign
        near, far = 0.0, s * depth
        edges: list[EdgeRec] = []

        def loop_edges(along: float, group: str) -> None:
            for i, shape in enumerate(f.sketch.entities):  # type: ignore[union-attr]
                for j, (pu, pv, length, max_f) in enumerate(_loop_picks(shape, depth)):
                    edges.append(
                        EdgeRec(
                            id=f"{f.name}:{group}:{i}.{j}",
                            feature=f.name,
                            group=group,
                            midpoint=frame.to_world(pu, pv, along),
                            length_mm=length,
                            max_fillet=max_f,
                            entity=i,
                            depth_span=depth,
                        )
                    )

        # top_loop = cap away from the sketch plane; bottom_loop = on it.
        loop_edges(far, "top_loop")
        loop_edges(near, "bottom_loop")

        for i, shape in enumerate(f.sketch.entities):
            if not isinstance(shape, g.Rect):
                continue  # circles/slots have no lateral corner edges
            corners = [
                (shape.xmin, shape.ymin),
                (shape.xmax, shape.ymin),
                (shape.xmax, shape.ymax),
                (shape.xmin, shape.ymax),
            ]
            for j, (cu, cv) in enumerate(corners):
                edges.append(
                    EdgeRec(
                        id=f"{f.name}:vertical_corners:{i}.{j}",
                        feature=f.name,
                        group="vertical_corners",
                        midpoint=frame.to_world(cu, cv, (near + far) / 2.0),
                        length_mm=depth,
                        # Hard bound for ONE corner alone: the full smaller
                        # side. Selecting several corners together triggers
                        # the shared-side pair rule (each < side/2).
                        max_fillet=min(shape.width, shape.height),
                        entity=i,
                        depth_span=depth,
                    )
                )
        return edges

    def _cut_feature_span(self, f: FeatureRec) -> tuple[float, float] | None:
        """Effective removed span of a cut along its family normal, mm.

        Nominal travel clamped to the material extent (blind cuts deeper
        than the part stop at its face; through-all cuts start at their
        sketch plane and run one way).
        """
        assert f.sketch is not None
        frame = f.sketch.frame
        interval = self.material_interval(frame.family)
        o = frame.offset
        s = f.direction_sign
        if f.through_all:
            if interval is None:
                return None
            lo, hi = interval
            if s > 0:
                lo = max(lo, o)
            else:
                hi = min(hi, o)
        else:
            depth = f.depth_mm or 0.0
            lo, hi = sorted((o, o + s * depth))
            if interval is not None:
                lo = max(lo, interval[0])
                hi = min(hi, interval[1])
        if hi - lo <= EPS:
            return None
        return lo, hi

    def _derive_cut_edges(self, f: FeatureRec) -> list[EdgeRec]:
        assert f.sketch is not None
        span = self._cut_feature_span(f)
        if span is None:
            return []
        frame = f.sketch.frame
        o = frame.offset
        priors = [
            p
            for p in self.features
            if p.kind == "cut" and p.sketch is not None and p.sketch.frame.family == frame.family
        ]
        edges: list[EdgeRec] = []
        for i, shape in enumerate(f.sketch.entities):
            lo, hi = span
            dropped: set[str] = set()
            # A rim only exists where material actually meets this cut. If a
            # prior cut already removed the surface at one of our boundaries
            # (a counterbore above a through-hole), the real rim sits at
            # that prior cut's floor — walk boundaries inward until stable.
            for _ in range(len(priors) + 1):
                changed = False
                for p in priors:
                    pspan = self._cut_feature_span(p)
                    if pspan is None or p.sketch is None:
                        continue
                    plo, phi = pspan
                    for pshape in p.sketch.entities:
                        if g.covers(pshape, shape):
                            if phi >= hi - EPS and plo <= hi + EPS and plo > lo + EPS:
                                hi = plo
                                changed = True
                            if plo <= lo + EPS and phi >= lo - EPS and phi < hi - EPS:
                                lo = phi
                                changed = True
                        elif not g.disjoint(pshape, shape):
                            # Partial overlap: the rim would be a composite
                            # curve this twin cannot place — drop it loudly
                            # rather than emit a pick point in air.
                            if phi >= hi - EPS and plo <= hi + EPS:
                                dropped.add("top_loop")
                            if plo <= lo + EPS and phi >= lo - EPS:
                                dropped.add("bottom_loop")
                if not changed:
                    break
            if hi - lo <= EPS:
                self._warn(
                    f"cut_extrude: no rim edges derived for {_describe(shape)} in "
                    f"{f.name}: earlier cuts already removed the surfaces it would "
                    "intersect"
                )
                continue
            d_span = hi - lo
            for group, along_world in (("top_loop", hi), ("bottom_loop", lo)):
                if group in dropped:
                    self._warn(
                        f"cut_extrude: {group} rim of {_describe(shape)} in {f.name} "
                        "partially overlaps an earlier cut; its edge is not "
                        "selectable by name-based selectors (use near_point if "
                        "needed)"
                    )
                    continue
                for j, (pu, pv, length, _mf) in enumerate(_loop_picks(shape, d_span)):
                    edges.append(
                        EdgeRec(
                            id=f"{f.name}:{group}:{i}.{j}",
                            feature=f.name,
                            group=group,
                            midpoint=frame.to_world(pu, pv, along_world - o),
                            length_mm=length,
                            # Opening rims are not bounded by the hole radius
                            # (a rim fillet rolls outward), only by the span.
                            max_fillet=d_span,
                            entity=i,
                            depth_span=d_span,
                        )
                    )
        return edges

    # -- selectors -----------------------------------------------------

    def resolve_edges(
        self,
        op: str,
        select: str | None,
        of_feature: str | None,
        near_point: Vec3 | None,
    ) -> list[EdgeRec]:
        self._require_part(op)
        if near_point is not None:
            candidates = [
                e for f in self.features for e in f.edges if e.consumed_by is None
            ]
            if not candidates:
                raise ModelError(f"{op}: the model has no selectable edges yet")
            best = min(
                candidates,
                key=lambda e: _dist3(e.midpoint, near_point),
            )
            d = _dist3(best.midpoint, near_point)
            if d > 10.0:
                self._warn(
                    f"{op}: near_point {list(near_point)} is {d:.1f} mm from the "
                    f"closest edge ({best.id}); check that this is the intended edge"
                )
            return [best]

        assert select is not None
        groups = EDGE_GROUPS if select == "all" else (select,)
        if of_feature is not None:
            feature = self.feature(of_feature)
        else:
            # Default to the most recent feature that has unconsumed edges
            # in the REQUESTED group — "fillet the corners" after drilling
            # holes should target the plate, not the hole cut.
            with_group = [
                f
                for f in self.features
                if any(e.consumed_by is None and e.group in groups for e in f.edges)
            ]
            if not with_group:
                raise ModelError(
                    f"{op}: no feature has unconsumed '{select}' edges; e.g. "
                    "cylinders and slots have no vertical corner edges"
                )
            feature = with_group[-1]
        edges = [
            e for e in feature.edges if e.group in groups and e.consumed_by is None
        ]
        if not edges:
            available = sorted({e.group for e in feature.edges if e.consumed_by is None})
            raise ModelError(
                f"{op}: feature {feature.name!r} has no unconsumed "
                f"'{select}' edges (available groups: {available or 'none'}); e.g. "
                "cylinders and slots have no vertical corner edges"
            )
        return edges

    def _check_edge_limits(
        self, op: str, term: str, value: float, edges: list[EdgeRec]
    ) -> None:
        # Per-edge hard bound (the edge alone).
        for e in edges:
            if e.max_fillet is not None and value >= e.max_fillet - EPS:
                raise ModelError(
                    f"{op}: {term} {value} mm is too large for edge {e.id}: it must "
                    f"be smaller than {e.max_fillet} mm (the smallest adjacent "
                    "dimension), or the feature consumes an adjacent face"
                )
        # Pair rule 1: opposing cap loops of one contour share the lateral
        # faces, so together each must stay under half the span.
        loops: dict[tuple[str, int], set[str]] = {}
        spans: dict[tuple[str, int], float] = {}
        for e in edges:
            if e.group in ("top_loop", "bottom_loop"):
                k = (e.feature, e.entity)
                loops.setdefault(k, set()).add(e.group)
                if e.depth_span is not None:
                    spans[k] = min(spans.get(k, e.depth_span), e.depth_span)
        for k, groups in loops.items():
            if {"top_loop", "bottom_loop"} <= groups and k in spans:
                bound = spans[k] / 2.0
                if value >= bound - EPS:
                    raise ModelError(
                        f"{op}: {term} {value} mm is too large with BOTH cap loops of "
                        f"{k[0]} selected: opposing edges share the {spans[k]} mm "
                        f"lateral faces, so each must be smaller than {bound} mm"
                    )
        # Pair rule 2: several corners of one rectangle share its sides.
        corners: dict[tuple[str, int], list[EdgeRec]] = {}
        for e in edges:
            if e.group == "vertical_corners":
                corners.setdefault((e.feature, e.entity), []).append(e)
        for k, es in corners.items():
            bounds = [e.max_fillet for e in es if e.max_fillet is not None]
            if len(es) >= 2 and bounds:
                bound = min(bounds) / 2.0
                if value >= bound - EPS:
                    raise ModelError(
                        f"{op}: {term} {value} mm is too large for {len(es)} corner "
                        f"edges of {k[0]} selected together: corner features on a "
                        f"shared side compete for it, so each must be smaller than "
                        f"{bound} mm"
                    )

    def fillet(
        self,
        radius: float,
        select: str | None,
        of_feature: str | None,
        near_point: Vec3 | None,
    ) -> tuple[FeatureRec, list[EdgeRec]]:
        edges = self.resolve_edges("fillet", select, of_feature, near_point)
        self._check_edge_limits("fillet", "radius", radius, edges)
        feature = FeatureRec(name=self._next_name("fillet"), kind="fillet")
        feature.detail["radius"] = radius
        feature.detail["edge_ids"] = [e.id for e in edges]
        for e in edges:
            e.consumed_by = feature.name
        self.features.append(feature)
        return feature, edges

    def chamfer(
        self,
        distance: float,
        angle: float,
        select: str | None,
        of_feature: str | None,
        near_point: Vec3 | None,
    ) -> tuple[FeatureRec, list[EdgeRec]]:
        edges = self.resolve_edges("chamfer", select, of_feature, near_point)
        self._check_edge_limits("chamfer", "distance", distance, edges)
        feature = FeatureRec(name=self._next_name("chamfer"), kind="chamfer")
        feature.detail["distance"] = distance
        feature.detail["angle"] = angle
        feature.detail["edge_ids"] = [e.id for e in edges]
        for e in edges:
            e.consumed_by = feature.name
        self.features.append(feature)
        return feature, edges

    # -- patterns ------------------------------------------------------

    def _pattern_seeds(self, op: str, names: list[str]) -> list[FeatureRec]:
        seeds = []
        for n in names:
            f = self.feature(n)
            if f.kind not in ("boss", "cut"):
                raise ModelError(
                    f"{op}: only boss/cut features can be patterned in v0.2, "
                    f"{n!r} is a {f.kind}"
                )
            seeds.append(f)
        return seeds

    def _require_axis(self, op: str, axis: AxisName) -> str:
        if axis not in self.axes:
            raise ModelError(
                f"{op}: reference axis {axis!r} does not exist; add a create_axis "
                "command first (macro-expanded pattern commands do this automatically)"
            )
        return AXIS_FEATURE_NAMES[axis]

    def linear_pattern(
        self,
        feature_names: list[str],
        direction: str,
        spacing: float,
        count: int,
        direction2: tuple[str, float, int] | None,
    ) -> FeatureRec:
        self._require_part("linear_pattern")
        seeds = self._pattern_seeds("linear_pattern", feature_names)
        dirs: list[tuple[str, float, int]] = [(direction, spacing, count)]
        if direction2 is not None:
            dirs.append(direction2)
        for d, _sp, _n in dirs:
            self._require_axis("linear_pattern", d.lstrip("-"))  # type: ignore[arg-type]

        feature = FeatureRec(name=self._next_name("linear_pattern"), kind="linear_pattern")
        feature.detail["seeds"] = list(feature_names)
        boss_fp: list[tuple[PlaneFamily, g.Shape]] = []
        cut_fp: list[tuple[PlaneFamily, g.Shape]] = []
        for seed in seeds:
            assert seed.sketch is not None
            frame = seed.sketch.frame
            offsets = _grid_offsets(dirs)
            for world_delta in offsets:
                if world_delta == (0.0, 0.0, 0.0):
                    continue
                du = dot(world_delta, frame.u)
                dv = dot(world_delta, frame.v)
                dn = dot(world_delta, frame.normal)
                if abs(dn) > EPS:
                    self._warn(
                        f"linear_pattern: instances of {seed.name} move along its "
                        "plane normal; the twin cannot validate stacked instances"
                    )
                    continue
                for shape in seed.sketch.entities:
                    moved = _translate(shape, du, dv)
                    if seed.kind == "boss":
                        boss_fp.append((frame.family, moved))
                    elif seed.through_all:
                        # Only through-all instances count as removed
                        # material — a patterned blind pocket leaves stock
                        # beneath, so later cuts there are legal (mirrors
                        # the through_all filter in _removed_footprints).
                        cut_fp.append((frame.family, moved))
                    if seed.kind == "cut" and not any(
                        g.contains(fp, moved) for fp in self._boss_footprints(frame.family)
                    ):
                        self._warn(
                            f"linear_pattern: instance of {seed.name} at offset "
                            f"({du:.3g}, {dv:.3g}) mm is not strictly inside the "
                            "material footprint; SolidWorks may reject that instance"
                        )
        feature.instance_boss_footprints = boss_fp
        feature.instance_cut_footprints = cut_fp
        self.features.append(feature)
        return feature

    def circular_pattern(
        self,
        feature_names: list[str],
        axis: AxisName,
        count: int,
        total_angle: float,
        equal_spacing: bool,
    ) -> FeatureRec:
        import math

        self._require_part("circular_pattern")
        seeds = self._pattern_seeds("circular_pattern", feature_names)
        self._require_axis("circular_pattern", axis)
        feature = FeatureRec(
            name=self._next_name("circular_pattern"), kind="circular_pattern"
        )
        feature.detail["seeds"] = list(feature_names)
        axis_vec = AXIS_VECTORS[axis]
        cut_fp: list[tuple[PlaneFamily, g.Shape]] = []
        boss_fp: list[tuple[PlaneFamily, g.Shape]] = []
        # Instance-angle semantics mirror SolidWorks: equal spacing over a
        # full 360 divides by count (no doubled instance at the seam); over
        # a partial angle the last instance lands ON the boundary, so both
        # equal and explicit spacing divide by count-1.
        if equal_spacing and abs(total_angle - 360.0) <= EPS:
            step = math.radians(total_angle) / count
        else:
            step = math.radians(total_angle) / max(count - 1, 1)
        for seed in seeds:
            assert seed.sketch is not None
            frame = seed.sketch.frame
            if abs(abs(dot(axis_vec, frame.normal)) - 1.0) > EPS:
                self._warn(
                    f"circular_pattern: axis {axis!r} is not normal to {seed.name}'s "
                    "sketch plane; rotated instances cannot be validated by the twin"
                )
                continue
            sign = 1.0 if dot(axis_vec, frame.normal) > 0 else -1.0
            for k in range(1, count):
                ang = sign * step * k
                for shape in seed.sketch.entities:
                    rotated = _rotate(shape, ang)
                    if rotated is None:
                        self._warn(
                            f"circular_pattern: rotated instances of a rectangle in "
                            f"{seed.name} are not axis-aligned and cannot be "
                            "containment-checked by the twin"
                        )
                        continue
                    if seed.kind == "boss":
                        boss_fp.append((frame.family, rotated))
                    elif seed.through_all:
                        # See linear_pattern: blind-pocket instances leave
                        # material beneath and must not count as removed.
                        cut_fp.append((frame.family, rotated))
                    if seed.kind == "cut" and not any(
                        g.contains(fp, rotated)
                        for fp in self._boss_footprints(frame.family)
                    ):
                        self._warn(
                            f"circular_pattern: instance {k} of {seed.name} is not "
                            "strictly inside the material footprint; SolidWorks may "
                            "reject that instance"
                        )
        feature.instance_boss_footprints = boss_fp
        feature.instance_cut_footprints = cut_fp
        self.features.append(feature)
        return feature

    # -- save / finalize / summary -------------------------------------

    def save_part(self, path: str) -> None:
        import os

        self._require_part("save_part")
        if self.active_sketch is not None:
            self._warn(
                f"save_part: sketch {self.active_sketch.name} is still open; "
                "the part will be saved mid-edit"
            )
        if not self.has_solid:
            self._warn("save_part: the part has no solid geometry yet")
        if not os.path.isabs(path):
            self._warn(
                f"save_part: '{path}' is relative; on Windows the COM backend resolves "
                "it against the swpilot working directory before calling SolidWorks, "
                "so its call log will show the absolute path"
            )
        self.saved_to.append(path)

    def finalize(self) -> None:
        for sketch in self.sketches:
            if sketch.consumed_by is None:
                self._warn(f"run finished with unconsumed sketch {sketch.name}")

    def summary(self) -> dict[str, object]:
        if not self.part_open:
            return {"part": None}
        return {
            "units": "mm",
            "planes": {
                name: {"family": f.family, "offset": f.offset}
                for name, f in self.planes.items()
            },
            "axes": sorted(AXIS_FEATURE_NAMES[a] for a in self.axes),
            "sketches": [
                {
                    "name": s.name,
                    "plane": s.frame.name,
                    "entities": [_shape_dict(e) for e in s.entities],
                    "consumed_by": s.consumed_by,
                }
                for s in self.sketches
            ],
            "features": [
                {
                    "name": f.name,
                    "kind": f.kind,
                    "sketch": f.sketch.name if f.sketch else None,
                    "depth_mm": f.depth_mm,
                    "through_all": f.through_all,
                    "reverse": f.reverse,
                    "draft_angle": f.draft_angle,
                    **(
                        {
                            "detail": {
                                k: v
                                for k, v in f.detail.items()
                                if not k.startswith("instance_")
                            }
                        }
                        if f.detail
                        else {}
                    ),
                }
                for f in self.features
            ],
            "saved_to": list(self.saved_to),
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _dist3(a: Vec3, b: Vec3) -> float:
    import math

    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _describe(shape: g.Shape) -> str:
    if isinstance(shape, g.Circle):
        return f"circle d={shape.diameter} mm at ({shape.cx}, {shape.cy})"
    if isinstance(shape, g.Rect):
        return f"rectangle {shape.width}x{shape.height} mm at ({shape.cx}, {shape.cy})"
    return f"slot w={shape.width} mm from ({shape.x1}, {shape.y1}) to ({shape.x2}, {shape.y2})"


def _shape_dict(s: g.Shape) -> dict[str, object]:
    if isinstance(s, g.Rect):
        return {
            "type": "rectangle",
            "center": [s.cx, s.cy],
            "width": s.width,
            "height": s.height,
        }
    if isinstance(s, g.Circle):
        return {"type": "circle", "center": [s.cx, s.cy], "diameter": s.diameter}
    return {
        "type": "slot",
        "start": [s.x1, s.y1],
        "end": [s.x2, s.y2],
        "width": s.width,
    }


LoopPick = tuple[float, float, float | None, float | None]


def _loop_picks(shape: g.Shape, depth: float) -> list[LoopPick]:
    """(u, v, edge_length, max_fillet) pick points for one cap loop.

    ``max_fillet`` is the bound for filleting ONE cap loop alone: in-plane
    it is limited by opposing edges of the same loop competing for the cap
    face (half the smaller side / the radius), along the extrusion by the
    FULL span — halving the span only applies when both cap loops are
    selected together, which the tracker's pair rule enforces.
    """
    if isinstance(shape, g.Rect):
        max_f = (
            min(shape.width / 2.0, shape.height / 2.0, depth) if depth > 0 else None
        )
        return [
            (shape.cx, shape.ymin, shape.width, max_f),
            (shape.xmax, shape.cy, shape.height, max_f),
            (shape.cx, shape.ymax, shape.width, max_f),
            (shape.xmin, shape.cy, shape.height, max_f),
        ]
    if isinstance(shape, g.Circle):
        max_f = min(shape.r, depth) if depth > 0 else shape.r
        import math

        return [(shape.cx + shape.r, shape.cy, math.pi * shape.diameter, max_f)]
    # slot: one outline edge pick at the apex beyond end 2
    length = shape.length
    if length <= 0:
        return []
    dx = (shape.x2 - shape.x1) / length
    dy = (shape.y2 - shape.y1) / length
    max_f = min(shape.width / 2.0, depth) if depth > 0 else shape.r
    return [(shape.x2 + dx * shape.r, shape.y2 + dy * shape.r, None, max_f)]


def _translate(shape: g.Shape, du: float, dv: float) -> g.Shape:
    if isinstance(shape, g.Rect):
        return g.Rect(shape.cx + du, shape.cy + dv, shape.width, shape.height)
    if isinstance(shape, g.Circle):
        return g.Circle(shape.cx + du, shape.cy + dv, shape.diameter)
    return g.Slot(shape.x1 + du, shape.y1 + dv, shape.x2 + du, shape.y2 + dv, shape.width)


def _rotate(shape: g.Shape, angle_rad: float) -> g.Shape | None:
    """Rotate about the sketch origin. Rects are not closed under rotation."""
    import math

    c, s = math.cos(angle_rad), math.sin(angle_rad)

    def rot(x: float, y: float) -> tuple[float, float]:
        return (x * c - y * s, x * s + y * c)

    if isinstance(shape, g.Circle):
        x, y = rot(shape.cx, shape.cy)
        return g.Circle(x, y, shape.diameter)
    if isinstance(shape, g.Slot):
        x1, y1 = rot(shape.x1, shape.y1)
        x2, y2 = rot(shape.x2, shape.y2)
        return g.Slot(x1, y1, x2, y2, shape.width)
    return None


def _grid_offsets(dirs: list[tuple[str, float, int]]) -> list[Vec3]:
    """World-space instance offsets for a 1- or 2-direction linear pattern."""

    def axis_delta(direction: str, spacing: float, k: int) -> Vec3:
        sign = -1.0 if direction.startswith("-") else 1.0
        ax = AXIS_VECTORS[direction.lstrip("-")]  # type: ignore[index]
        return (ax[0] * sign * spacing * k, ax[1] * sign * spacing * k, ax[2] * sign * spacing * k)

    d1, sp1, n1 = dirs[0]
    seconds: list[tuple[str, float, int]] = dirs[1:]
    out: list[Vec3] = []
    for i in range(n1):
        base = axis_delta(d1, sp1, i)
        if not seconds:
            out.append(base)
            continue
        d2, sp2, n2 = seconds[0]
        for j in range(n2):
            extra = axis_delta(d2, sp2, j)
            out.append((base[0] + extra[0], base[1] + extra[1], base[2] + extra[2]))
    return out
