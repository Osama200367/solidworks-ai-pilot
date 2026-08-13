"""Smart dimensioning: governing features only, never a dump.

Given a :class:`~swpilot.model.drawing.DrawingTracker`, emit the
dimension set a machinist actually needs:

* overall envelope — W/H/T for rectangular parts, outer diameters plus
  length for turned parts (silhouette-detected)
* hole callouts in N x diameter form (counterbore/countersink data on a
  second line) with position dimensions from the datum edges
* pattern pitch for linear hole patterns; a bolt-circle note for
  circular ones
* fillet/chamfer/slot data as a note block under the front view

Every dimension is a typed :class:`DimSpec` whose pick points are model
geometry projected into sheet coordinates. Selection robustness rule:
picks prefer positions whose projection collapses the uncertain axis
(e.g. the front view collapses z), so consumed or shrunk edges still
project onto the same outline line. Anything the analyzer cannot place
safely is *skipped with a warning* — silence is never an answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from swpilot.model import geometry as g
from swpilot.model.assembly import AssemblyTracker
from swpilot.model.drawing import DimSpec, DrawingTracker, NoteSpec, ViewRec
from swpilot.model.tracker import FeatureRec, ModelTracker
from swpilot.tolerances import EPS

Vec3 = tuple[float, float, float]

# Which standard view looks along each plane family's normal (shows its
# sketches true-shape), and which views show the family-normal extent.
TRUE_VIEW: dict[str, str] = {"front": "front", "top": "top", "right": "right"}
THICKNESS_VIEWS: dict[str, list[str]] = {
    "front": ["right", "top"],
    "top": ["front", "right"],
    "right": ["front", "top"],
}

DIM_OFFSET = 8.0
DIM_STAGGER = 6.0


def _fmt(v: float) -> str:
    return f"{v:g}"


def _as_int(v: object) -> int:
    assert isinstance(v, int)
    return v


@dataclass
class _TurnedProfile:
    """A part that is a stack of concentric cylinders (plus cuts)."""

    family: str
    center: tuple[float, float]  # sketch coordinates shared by all bosses
    intervals: list[tuple[float, float, float]]  # (n0, n1, radius) along the normal
    bore_radius: float | None  # concentric through-bore, if any
    bore_span: tuple[float, float] | None


def analyze(
    d: DrawingTracker,
) -> tuple[list[DimSpec], list[NoteSpec], list[str]]:
    if isinstance(d.model, AssemblyTracker):
        return _Analyzer(d).assembly()
    return _Analyzer(d).part()


class _Analyzer:
    def __init__(self, d: DrawingTracker) -> None:
        self.d = d
        self.dims: list[DimSpec] = []
        self.notes: list[NoteSpec] = []
        self.warnings: list[str] = []
        self._below_i: dict[str, int] = {}  # per-view stagger counters
        self._left_i: dict[str, int] = {}

    # -- placement helpers ---------------------------------------------

    def _view(self, name: str) -> ViewRec | None:
        return self.d.views.get(name)

    def _below(self, view: ViewRec) -> tuple[float, float]:
        i = self._below_i.get(view.name, 0)
        self._below_i[view.name] = i + 1
        return (view.center[0], view.center[1] - view.size[1] / 2.0 - DIM_OFFSET - DIM_STAGGER * i)

    def _left(self, view: ViewRec) -> tuple[float, float]:
        i = self._left_i.get(view.name, 0)
        self._left_i[view.name] = i + 1
        return (view.center[0] - view.size[0] / 2.0 - DIM_OFFSET - DIM_STAGGER * i, view.center[1])

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def _add(self, dim: DimSpec) -> None:
        self.dims.append(dim)

    def _note_lines(self, view: ViewRec, lines: list[str]) -> None:
        from swpilot.model.drawing import NOTE_LINE

        x = view.center[0] - view.size[0] / 2.0
        y0 = view.center[1] - view.size[1] / 2.0 - DIM_OFFSET - 2 * DIM_STAGGER - 4.0
        for i, text in enumerate(lines):
            self.notes.append(NoteSpec(text=text, position=(x, y0 - i * NOTE_LINE)))

    # -- part analysis -------------------------------------------------

    def part(self) -> tuple[list[DimSpec], list[NoteSpec], list[str]]:
        model = self.d.model
        assert isinstance(model, ModelTracker)
        bosses = [f for f in model.features if f.kind == "boss"]
        if not bosses:
            self._warn("smart_dimensions: the part has no solid geometry")
            return self.dims, self.notes, self.warnings
        base = bosses[0]
        assert base.sketch is not None
        family = base.sketch.frame.family

        turned = self._turned_profile(model)
        if turned is not None:
            self._turned_envelope(turned)
        else:
            self._rect_envelope(model, base, family)
        self._hole_callouts(model, turned)
        self._feature_notes(model, family)
        return self.dims, self.notes, self.warnings

    # -- envelope: rectangular -----------------------------------------

    def _rect_envelope(self, model: ModelTracker, base: FeatureRec, family: str) -> None:
        assert base.sketch is not None
        tv = self._view(TRUE_VIEW[family])
        frame = base.sketch.frame
        rect = base.sketch.entities[0] if len(base.sketch.entities) == 1 else None
        if not isinstance(rect, g.Rect):
            self._warn(
                "smart_dimensions: envelope skipped — the base feature is not a "
                "single rectangle (and the part is not a turned profile)"
            )
            return
        if tv is None:
            self._warn(
                f"smart_dimensions: envelope W/H skipped — add the "
                f"{TRUE_VIEW[family]!r} view (true view of the base sketch plane)"
            )
        else:
            # Two-pick dims between opposite outline lines: robust against
            # corner fillets (a single bottom edge would measure W - 2r).
            self._add(
                DimSpec(
                    name="envelope_width",
                    view=tv.name,
                    kind="linear",
                    picks=[
                        self.d.sheet_point(tv.name, frame.to_world(rect.xmin, rect.cy)),
                        self.d.sheet_point(tv.name, frame.to_world(rect.xmax, rect.cy)),
                    ],
                    placement=self._below(tv),
                    value=rect.width,
                )
            )
            self._add(
                DimSpec(
                    name="envelope_height",
                    view=tv.name,
                    kind="linear",
                    picks=[
                        self.d.sheet_point(tv.name, frame.to_world(rect.cx, rect.ymin)),
                        self.d.sheet_point(tv.name, frame.to_world(rect.cx, rect.ymax)),
                    ],
                    placement=self._left(tv),
                    value=rect.height,
                )
            )
        depth = base.depth_mm or 0.0
        s = base.direction_sign
        n0, n1 = sorted((0.0, s * depth))
        self._normal_extent_dim(
            "thickness", family, n0, n1, frame.to_world(rect.cx, rect.cy), (rect.cy, rect.cy)
        )

    def _normal_extent_dim(
        self,
        name: str,
        family: str,
        n0: float,
        n1: float,
        center_world: Vec3,
        v_picks: tuple[float, float],
        prefix: str | None = None,
    ) -> None:
        """Two-pick dim between the cap planes at n0/n1 along the family normal.

        The thickness view collapses the in-plane axis the cap edges run
        along, so the picks land on the projected cap lines regardless of
        chamfers or fillets that moved individual edges.
        """
        view = None
        for cand in THICKNESS_VIEWS[family]:
            view = self._view(cand)
            if view is not None:
                break
        if view is None:
            self._warn(
                f"smart_dimensions: {name} skipped — add one of "
                f"{THICKNESS_VIEWS[family]} to show the part's normal extent"
            )
            return
        from swpilot.model.planes import standard_frame

        frame = standard_frame(family)  # type: ignore[arg-type]
        base_uv = _project_uv(frame, center_world)
        p0 = frame.to_world(base_uv[0], v_picks[0], n0)
        p1 = frame.to_world(base_uv[0], v_picks[1], n1)
        self._add(
            DimSpec(
                name=name,
                view=view.name,
                kind="linear",
                picks=[
                    self.d.sheet_point(view.name, p0),
                    self.d.sheet_point(view.name, p1),
                ],
                placement=self._below(view),
                value=n1 - n0,
                prefix=prefix,
            )
        )

    # -- envelope: turned ----------------------------------------------

    def _turned_profile(self, model: ModelTracker) -> _TurnedProfile | None:
        bosses = [f for f in model.features if f.kind == "boss"]
        if not bosses:
            return None
        center: tuple[float, float] | None = None
        family: str | None = None
        intervals: list[tuple[float, float, float]] = []
        for f in bosses:
            assert f.sketch is not None
            if len(f.sketch.entities) != 1 or not isinstance(f.sketch.entities[0], g.Circle):
                return None
            c = f.sketch.entities[0]
            if family is None:
                family = f.sketch.frame.family
                center = (c.cx, c.cy)
            elif f.sketch.frame.family != family:
                return None
            assert center is not None
            if abs(c.cx - center[0]) > EPS or abs(c.cy - center[1]) > EPS:
                return None
            o = f.sketch.frame.offset
            depth = f.depth_mm or 0.0
            n0, n1 = sorted((o, o + f.direction_sign * depth))
            intervals.append((n0, n1, c.r))
        assert family is not None and center is not None
        intervals.sort()
        bore_r: float | None = None
        bore_span: tuple[float, float] | None = None
        for f in model.features:
            if f.kind != "cut" or f.sketch is None:
                continue
            if f.sketch.frame.family != family or len(f.sketch.entities) != 1:
                continue
            c2 = f.sketch.entities[0]
            if not isinstance(c2, g.Circle):
                continue
            if abs(c2.cx - center[0]) > EPS or abs(c2.cy - center[1]) > EPS:
                continue
            span = model._cut_feature_span(f)
            if span is None:
                continue
            if bore_r is None or c2.r < bore_r:
                bore_r = c2.r
                bore_span = span
        return _TurnedProfile(
            family=family,
            center=center,
            intervals=intervals,
            bore_radius=bore_r,
            bore_span=bore_span,
        )

    def _section_view(self) -> ViewRec | None:
        for name in self.d.view_order:
            if self.d.views[name].kind == "section":
                return self.d.views[name]
        return None

    def _turned_envelope(self, t: _TurnedProfile) -> None:
        from swpilot.model.planes import standard_frame

        frame = standard_frame(t.family)  # type: ignore[arg-type]
        cx, cy = t.center
        tv = self._view(TRUE_VIEW[t.family])
        # Outer diameters on the true-circle view, largest first.
        radii = sorted({r for _, _, r in t.intervals}, reverse=True)
        if tv is None:
            self._warn(
                f"smart_dimensions: outer diameters skipped — add the "
                f"{TRUE_VIEW[t.family]!r} view (true-circle view)"
            )
        else:
            for i, r in enumerate(radii):
                a = math.radians(45.0)
                pick = frame.to_world(cx + r * math.cos(a), cy + r * math.sin(a))
                sp = self.d.sheet_point(tv.name, pick)
                self._add(
                    DimSpec(
                        name=f"od_{i + 1}" if len(radii) > 1 else "od",
                        view=tv.name,
                        kind="diameter",
                        picks=[sp],
                        placement=(sp[0] + 10.0 + 6.0 * i, sp[1] + 10.0 + 6.0 * i),
                        value=2.0 * r,
                    )
                )

        sec = self._section_view()
        n_lo = min(n0 for n0, _, _ in t.intervals)
        n_hi = max(n1 for _, n1, _ in t.intervals)
        rb = t.bore_radius or 0.0

        def ring_mid(n: float) -> float:
            """v pick inside the material ring of the face plane at n.

            End faces span bore-to-outer; a step face at an internal
            boundary is the annulus between the two adjacent radii.
            """
            touching = [
                r
                for n0, n1, r in t.intervals
                if abs(n0 - n) <= EPS or abs(n1 - n) <= EPS
            ]
            if len(touching) >= 2 and max(touching) - min(touching) > EPS:
                return cy + (min(touching) + max(touching)) / 2.0
            outer = max(touching) if touching else max(r for _, _, r in t.intervals)
            return cy + (rb + outer) / 2.0

        if sec is not None:
            # Overall length between the two end-face lines of the section
            # profile, then one datum dim per internal step boundary.
            boundaries = sorted({n for n0, n1, _ in t.intervals for n in (n0, n1)})
            internal = [n for n in boundaries if n_lo + EPS < n < n_hi - EPS]
            self._add(
                DimSpec(
                    name="length",
                    view=sec.name,
                    kind="linear",
                    picks=[
                        self.d.sheet_point(sec.name, frame.to_world(cx, ring_mid(n_lo), n_lo)),
                        self.d.sheet_point(sec.name, frame.to_world(cx, ring_mid(n_hi), n_hi)),
                    ],
                    placement=self._below(sec),
                    value=n_hi - n_lo,
                )
            )
            for i, n in enumerate(internal[:2]):
                self._add(
                    DimSpec(
                        name=f"step_{i + 1}",
                        view=sec.name,
                        kind="linear",
                        picks=[
                            self.d.sheet_point(
                                sec.name, frame.to_world(cx, ring_mid(n_lo), n_lo)
                            ),
                            self.d.sheet_point(sec.name, frame.to_world(cx, ring_mid(n), n)),
                        ],
                        placement=self._below(sec),
                        value=n - n_lo,
                    )
                )
            if len(internal) > 2:
                self._warn(
                    "smart_dimensions: only the first 2 step boundaries were "
                    "dimensioned; add the rest manually"
                )
            if t.bore_radius is not None and t.bore_span is not None:
                z_mid = (t.bore_span[0] + t.bore_span[1]) / 2.0
                p_top = frame.to_world(cx, cy + t.bore_radius, z_mid)
                p_bot = frame.to_world(cx, cy - t.bore_radius, z_mid)
                sp = self.d.sheet_point(sec.name, p_top)
                self._add(
                    DimSpec(
                        name="bore",
                        view=sec.name,
                        kind="linear",
                        picks=[sp, self.d.sheet_point(sec.name, p_bot)],
                        placement=(
                            sec.center[0] + sec.size[0] / 2.0 + DIM_OFFSET,
                            sec.center[1],
                        ),
                        value=2.0 * t.bore_radius,
                        prefix="<MOD-DIAM>",
                    )
                )
        else:
            self._warn(
                "smart_dimensions: no section view — the bore and length are "
                "dimensioned on outside views; add section_view for a "
                "machinist-preferred sheet"
            )
            self._normal_extent_dim(
                "length", t.family, n_lo, n_hi,
                frame.to_world(cx, cy), (ring_mid(n_lo), ring_mid(n_hi)),
            )
            if t.bore_radius is not None and tv is not None:
                a = math.radians(225.0)
                pick = frame.to_world(
                    cx + t.bore_radius * math.cos(a), cy + t.bore_radius * math.sin(a)
                )
                sp = self.d.sheet_point(tv.name, pick)
                self._add(
                    DimSpec(
                        name="bore",
                        view=tv.name,
                        kind="diameter",
                        picks=[sp],
                        placement=(sp[0] - 12.0, sp[1] - 12.0),
                        value=2.0 * t.bore_radius,
                    )
                )

    # -- hole callouts --------------------------------------------------

    def _hole_features(self, model: ModelTracker) -> list[FeatureRec]:
        out = []
        for f in model.features:
            if f.kind != "cut" or f.sketch is None or not f.sketch.entities:
                continue
            if all(isinstance(e, g.Circle) for e in f.sketch.entities):
                out.append(f)
        return out

    def _pattern_multiplier(self, model: ModelTracker, feature: FeatureRec) -> int:
        n = 1
        for f in model.features:
            if f.kind not in ("linear_pattern", "circular_pattern"):
                continue
            seeds = f.detail.get("seeds")
            if not isinstance(seeds, list) or feature.name not in seeds:
                continue
            n *= _as_int(f.detail.get("count", 1))
            d2 = f.detail.get("direction2")
            if f.kind == "linear_pattern" and isinstance(d2, dict):
                n *= _as_int(d2.get("count", 1))
        return n

    def _circular_pattern_of(
        self, model: ModelTracker, feature: FeatureRec
    ) -> FeatureRec | None:
        for f in model.features:
            if f.kind != "circular_pattern":
                continue
            seeds = f.detail.get("seeds")
            if isinstance(seeds, list) and feature.name in seeds:
                return f
        return None

    def _linear_patterns_of(
        self, model: ModelTracker, feature: FeatureRec
    ) -> list[FeatureRec]:
        return [
            f
            for f in model.features
            if f.kind == "linear_pattern"
            and isinstance(f.detail.get("seeds"), list)
            and feature.name in f.detail["seeds"]  # type: ignore[operator]
        ]

    def _hole_callouts(self, model: ModelTracker, turned: _TurnedProfile | None) -> None:
        holes = self._hole_features(model)
        if not holes:
            return
        # Pair counterbore/countersink cuts with their through-holes: a
        # blind concentric cut over the same centers, larger diameter.
        def centers(f: FeatureRec) -> set[tuple[float, float]]:
            assert f.sketch is not None
            return {
                (round(e.cx, 6), round(e.cy, 6))
                for e in f.sketch.entities
                if isinstance(e, g.Circle)
            }

        paired: dict[str, FeatureRec] = {}  # through-hole name -> cb/csk feature
        consumed: set[str] = set()
        for fa in holes:
            if fa.through_all:
                for fb in holes:
                    if fb is fa or fb.through_all or fb.name in consumed:
                        continue
                    ca, cb = centers(fa), centers(fb)
                    assert fa.sketch is not None and fb.sketch is not None
                    ra = max(e.r for e in fa.sketch.entities if isinstance(e, g.Circle))
                    rbm = min(e.r for e in fb.sketch.entities if isinstance(e, g.Circle))
                    if ca == cb and rbm > ra:
                        paired[fa.name] = fb
                        consumed.add(fb.name)
                        break

        for f in holes:
            if f.name in consumed:
                continue  # reported as part of its through-hole's callout
            assert f.sketch is not None
            family = f.sketch.frame.family
            circles = [e for e in f.sketch.entities if isinstance(e, g.Circle)]
            if turned is not None and len(circles) == 1:
                c = circles[0]
                if (
                    abs(c.cx - turned.center[0]) <= EPS
                    and abs(c.cy - turned.center[1]) <= EPS
                ):
                    continue  # the bore; dimensioned by the turned flow
            tv = self._view(TRUE_VIEW[family])
            if tv is None:
                self._warn(
                    f"smart_dimensions: hole callout for {f.name} skipped — add "
                    f"the {TRUE_VIEW[family]!r} view where its circles are true"
                )
                continue
            frame = f.sketch.frame
            diam = 2.0 * circles[0].r
            if any(abs(e.r - circles[0].r) > EPS for e in circles):
                self._warn(
                    f"smart_dimensions: {f.name} mixes hole diameters; callout "
                    "uses the first and the count covers all"
                )
            n = len(circles) * self._pattern_multiplier(model, f)
            datum_c = min(circles, key=lambda e: (e.cx, e.cy))
            a = math.radians(45.0)
            pick3 = frame.to_world(
                datum_c.cx + datum_c.r * math.cos(a), datum_c.cy + datum_c.r * math.sin(a)
            )
            sp = self.d.sheet_point(tv.name, pick3)
            away = (
                1.0 if sp[0] >= tv.center[0] else -1.0,
                1.0 if sp[1] >= tv.center[1] else -1.0,
            )
            below = None
            pair = paired.get(f.name)
            if pair is not None:
                assert pair.sketch is not None
                pd = 2.0 * max(
                    e.r for e in pair.sketch.entities if isinstance(e, g.Circle)
                )
                if pair.draft_angle is not None:
                    # The drafted cone cut is the countersink. The hole macro
                    # sketches it at cs_diameter ON the surface (necking down
                    # with depth), so its largest circle IS the major
                    # diameter; the included angle is twice the draft.
                    below = (
                        f"<HOLE-SINK><MOD-DIAM>{_fmt(pd)} X "
                        f"{_fmt(2 * pair.draft_angle)}<MOD-DEG>"
                    )
                else:
                    below = (
                        f"<HOLE-SPOT><MOD-DIAM>{_fmt(pd)} "
                        f"<HOLE-DEPTH>{_fmt(pair.depth_mm or 0.0)}"
                    )
            self._add(
                DimSpec(
                    name=f"callout_{f.name}",
                    view=tv.name,
                    kind="diameter",
                    picks=[sp],
                    placement=(sp[0] + 12.0 * away[0], sp[1] + 12.0 * away[1]),
                    value=diam,
                    prefix=f"{n}X " if n > 1 else None,
                    below=below,
                )
            )
            circ = self._circular_pattern_of(model, f)
            if circ is not None:
                bcd = 2.0 * math.hypot(datum_c.cx, datum_c.cy)
                self._note_lines(
                    tv,
                    [
                        f"<MOD-DIAM>{_fmt(diam)} HOLES EQUALLY SPACED ON "
                        f"<MOD-DIAM>{_fmt(bcd)} B.C."
                    ],
                )
            elif turned is None:
                self._position_dims(model, f, datum_c, tv, frame)
            for lp in self._linear_patterns_of(model, f):
                self._pitch_dims(f, datum_c, lp, tv, frame)

    def _position_dims(
        self,
        model: ModelTracker,
        f: FeatureRec,
        datum_c: g.Circle,
        tv: ViewRec,
        frame: object,
    ) -> None:
        """Datum-edge position dims for the datum hole of a rect part."""
        bosses = [b for b in model.features if b.kind == "boss"]
        base = bosses[0]
        assert base.sketch is not None
        rect = base.sketch.entities[0] if len(base.sketch.entities) == 1 else None
        if not isinstance(rect, g.Rect) or base.sketch.frame.family != f.sketch.frame.family:  # type: ignore[union-attr]
            self._warn(
                f"smart_dimensions: position dims for {f.name} skipped — no "
                "rectangular datum edges on its plane family"
            )
            return
        fr = base.sketch.frame
        a = math.radians(45.0)
        circle_pick = self.d.sheet_point(
            tv.name,
            fr.to_world(
                datum_c.cx + datum_c.r * math.cos(a), datum_c.cy + datum_c.r * math.sin(a)
            ),
        )
        edge_x = self.d.sheet_point(tv.name, fr.to_world(rect.xmin, datum_c.cy))
        edge_y = self.d.sheet_point(tv.name, fr.to_world(datum_c.cx, rect.ymin))
        self._add(
            DimSpec(
                name=f"pos_x_{f.name}",
                view=tv.name,
                kind="linear",
                picks=[edge_x, circle_pick],
                placement=self._below(tv),
                value=datum_c.cx - rect.xmin,
            )
        )
        self._add(
            DimSpec(
                name=f"pos_y_{f.name}",
                view=tv.name,
                kind="linear",
                picks=[edge_y, circle_pick],
                placement=self._left(tv),
                value=datum_c.cy - rect.ymin,
            )
        )

    def _pitch_dims(
        self,
        f: FeatureRec,
        datum_c: g.Circle,
        pattern: FeatureRec,
        tv: ViewRec,
        frame: object,
    ) -> None:
        from swpilot.model.planes import AXIS_VECTORS

        assert f.sketch is not None
        fr = f.sketch.frame
        dirs: list[tuple[str, float]] = [
            (str(pattern.detail["direction"]), float(pattern.detail["spacing"]))  # type: ignore[arg-type]
        ]
        d2 = pattern.detail.get("direction2")
        if isinstance(d2, dict):
            dirs.append((str(d2["direction"]), float(d2["spacing"])))
        for i, (direction, spacing) in enumerate(dirs):
            sign = -1.0 if direction.startswith("-") else 1.0
            ax = AXIS_VECTORS[direction.lstrip("-")]  # type: ignore[index]
            from swpilot.model.planes import dot

            du = dot(ax, fr.u) * sign * spacing
            dv = dot(ax, fr.v) * sign * spacing
            if abs(du) <= EPS and abs(dv) <= EPS:
                self._warn(
                    f"smart_dimensions: pitch of {pattern.name} is normal to the "
                    "sketch plane and cannot be dimensioned in the true view"
                )
                continue
            a = math.radians(45.0)
            p0 = fr.to_world(
                datum_c.cx + datum_c.r * math.cos(a), datum_c.cy + datum_c.r * math.sin(a)
            )
            p1 = fr.to_world(
                datum_c.cx + du + datum_c.r * math.cos(a),
                datum_c.cy + dv + datum_c.r * math.sin(a),
            )
            s0 = self.d.sheet_point(tv.name, p0)
            s1 = self.d.sheet_point(tv.name, p1)
            mid = ((s0[0] + s1[0]) / 2.0, (s0[1] + s1[1]) / 2.0)
            horizontal = abs(s1[0] - s0[0]) >= abs(s1[1] - s0[1])
            off_y = -10.0 if mid[1] <= tv.center[1] else 10.0
            off_x = -10.0 if mid[0] <= tv.center[0] else 10.0
            placement = (mid[0], mid[1] + off_y) if horizontal else (mid[0] + off_x, mid[1])
            self._add(
                DimSpec(
                    name=f"pitch_{pattern.name}" + (f"_{i + 1}" if len(dirs) > 1 else ""),
                    view=tv.name,
                    kind="linear",
                    picks=[s0, s1],
                    placement=placement,
                    value=spacing,
                )
            )

    # -- notes ----------------------------------------------------------

    def _feature_notes(self, model: ModelTracker, family: str) -> None:
        tv = self._view(TRUE_VIEW[family]) or next(
            (self.d.views[n] for n in self.d.view_order if self.d.views[n].kind != "iso"),
            None,
        )
        if tv is None:
            return
        lines: list[str] = []
        fillets: dict[float, int] = {}
        chamfers: dict[tuple[float, float], int] = {}
        for f in model.features:
            if f.kind == "fillet":
                r = float(f.detail["radius"])  # type: ignore[arg-type]
                fillets[r] = fillets.get(r, 0) + len(f.detail.get("edge_ids", []))  # type: ignore[arg-type]
            elif f.kind == "chamfer":
                key = (float(f.detail["distance"]), float(f.detail["angle"]))  # type: ignore[arg-type]
                chamfers[key] = chamfers.get(key, 0) + len(f.detail.get("edge_ids", []))  # type: ignore[arg-type]
        for r, n in sorted(fillets.items()):
            lines.append(f"FILLETS R{_fmt(r)} ({n} PLCS)")
        for (dist, ang), n in sorted(chamfers.items()):
            lines.append(f"CHAMFERS {_fmt(dist)} X {_fmt(ang)}<MOD-DEG> ({n} PLCS)")
        for f in model.features:
            if f.kind != "cut" or f.sketch is None:
                continue
            slots = [e for e in f.sketch.entities if isinstance(e, g.Slot)]
            for s in slots:
                n_total = self._pattern_multiplier(model, f)
                pitch = ""
                lps = self._linear_patterns_of(model, f)
                if lps:
                    pitch = f", PITCH {_fmt(float(lps[0].detail['spacing']))}"  # type: ignore[arg-type]
                count = f"{n_total}X " if n_total > 1 else ""
                lines.append(
                    f"{count}SLOT W{_fmt(s.width)} X {_fmt(s.length)} C-C{pitch}"
                )
        if lines:
            self._note_lines(tv, lines)

    # -- assembly analysis ----------------------------------------------

    def assembly(self) -> tuple[list[DimSpec], list[NoteSpec], list[str]]:
        asm = self.d.model
        assert isinstance(asm, AssemblyTracker)
        tv = self._view("front")
        if tv is None:
            self._warn(
                "smart_dimensions: assembly envelope skipped — add the front view"
            )
            return self.dims, self.notes, self.warnings
        lo, hi = self.d.aabb
        # Envelope W/H via boundary silhouette lines; picks use the mid of
        # the boundary component's own extent so they land on real edges.
        for name, axis, placement in (
            ("envelope_width", 0, "below"),
            ("envelope_height", 1, "left"),
        ):
            picks = []
            ok = True
            for bound in (lo[axis], hi[axis]):
                comp = next(
                    (
                        c
                        for c in asm.components.values()
                        if abs(c.world_aabb()[0][axis] - bound) <= EPS
                        or abs(c.world_aabb()[1][axis] - bound) <= EPS
                    ),
                    None,
                )
                if comp is None:
                    ok = False
                    break
                clo, chi = comp.world_aabb()
                other = 1 - axis
                p = [0.0, 0.0, (clo[2] + chi[2]) / 2.0]
                p[axis] = bound
                p[other] = (clo[other] + chi[other]) / 2.0
                picks.append(self.d.sheet_point(tv.name, (p[0], p[1], p[2])))
            if not ok:
                self._warn(f"smart_dimensions: {name} skipped — no boundary component found")
                continue
            self._add(
                DimSpec(
                    name=name,
                    view=tv.name,
                    kind="linear",
                    picks=picks,
                    placement=self._below(tv) if placement == "below" else self._left(tv),
                    value=hi[axis] - lo[axis],
                )
            )
        self._warn(
            "smart_dimensions: assembly sheets carry envelope dims only; "
            "dimension component features on their own part sheets"
        )
        return self.dims, self.notes, self.warnings


def _project_uv(frame: object, p: Vec3) -> tuple[float, float]:
    from swpilot.model.planes import PlaneFrame, dot

    assert isinstance(frame, PlaneFrame)
    return (dot(p, frame.u), dot(p, frame.v))
