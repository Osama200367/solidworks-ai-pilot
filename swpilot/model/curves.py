"""Parametric curve generators for the v0.5 curves engine.

Pure math, importable everywhere, fully CI-tested. Each generator returns
both the *invariants* the digital twin validates exactly and the ordered
*profile segments* (splines / arcs / lines) the COM backend draws. The
twin can verify every scalar here; only the fidelity of the SolidWorks
spline fit and the validity of the resulting solid are Windows-only (see
WINDOWS_SETUP.md).

Standard metric gear math (ISO 53 / DIN 867 reference rack, unmodified):

* pitch diameter    d  = m·z
* base diameter     db = d·cos α
* addendum          = m         → tip diameter da = m·(z + 2)
* dedendum          = 1.25·m    → root diameter df = m·(z − 2.5)
* the involute of the base circle unwinds by roll angle t:
      x(t) = rb·(cos t + t·sin t),  y(t) = rb·(sin t − t·cos t)
  a point sits at radius r(t) = rb·√(1+t²) and polar angle t − atan(t).

All lengths mm, angles handled in radians internally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


# --------------------------------------------------------------------------
# Profile segments: what the backend sketches
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SplineSeg:
    """An interpolated spline through an ordered point list."""

    points: tuple[Point, ...]

    def endpoints(self) -> tuple[Point, Point]:
        return self.points[0], self.points[-1]


@dataclass(frozen=True)
class ArcSeg:
    """A circular arc by center + two endpoints (SolidWorks CreateArc)."""

    center: Point
    start: Point
    end: Point
    ccw: bool

    def endpoints(self) -> tuple[Point, Point]:
        return self.start, self.end


@dataclass(frozen=True)
class LineSeg:
    start: Point
    end: Point

    def endpoints(self) -> tuple[Point, Point]:
        return self.start, self.end


ProfileSeg = SplineSeg | ArcSeg | LineSeg


def _rot(p: Point, ang: float) -> Point:
    c, s = math.cos(ang), math.sin(ang)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def seg_endpoints(seg: ProfileSeg) -> tuple[Point, Point]:
    return seg.endpoints()


def loop_is_closed(segs: list[ProfileSeg], tol: float = 1e-6) -> bool:
    """True if consecutive segment endpoints meet and the loop closes."""
    if not segs:
        return False
    for a, b in zip(segs, segs[1:], strict=False):
        if _dist(a.endpoints()[1], b.endpoints()[0]) > tol:
            return False
    return _dist(segs[-1].endpoints()[1], segs[0].endpoints()[0]) <= tol


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def sample_segment(seg: ProfileSeg, n: int = 10) -> list[Point]:
    """Approximate a segment as points (for bbox / envelope math)."""
    if isinstance(seg, SplineSeg):
        return list(seg.points)
    if isinstance(seg, LineSeg):
        return [seg.start, seg.end]
    # arc: walk the swept angle from start to end about the center
    cx, cy = seg.center
    a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a1 = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    r = math.hypot(seg.start[0] - cx, seg.start[1] - cy)
    if seg.ccw and a1 <= a0:
        a1 += 2 * math.pi
    if not seg.ccw and a1 >= a0:
        a1 -= 2 * math.pi
    return [
        (cx + r * math.cos(a0 + (a1 - a0) * i / (n - 1)),
         cy + r * math.sin(a0 + (a1 - a0) * i / (n - 1)))
        for i in range(n)
    ]


def segments_points(segs: list[ProfileSeg], n: int = 10) -> list[Point]:
    out: list[Point] = []
    for s in segs:
        out.extend(sample_segment(s, n))
    return out


def segments_bbox(segs: list[ProfileSeg]) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) of a segment loop in sketch space."""
    pts = segments_points(segs)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def segments_radial_extent(
    segs: list[ProfileSeg], center: Point = (0.0, 0.0)
) -> tuple[float, float]:
    """(min, max) distance of a segment loop from a center point (mm)."""
    radii = [_dist(p, center) for p in segments_points(segs)]
    return min(radii), max(radii)


# --------------------------------------------------------------------------
# Involute spur gear
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GearInvariants:
    """Everything the twin verifies exactly for an involute gear."""

    module: float
    teeth: int
    pressure_angle_deg: float
    pitch_dia: float
    base_dia: float
    tip_dia: float
    root_dia: float
    tooth_thickness_pitch: float  # circular tooth thickness at pitch, mm
    fillet_radius: float
    undercut: bool
    pointed_tip: bool
    sub_base_flank: bool  # part of the active flank lies below the base circle

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "teeth": self.teeth,
            "pressure_angle_deg": self.pressure_angle_deg,
            "pitch_dia": round(self.pitch_dia, 6),
            "base_dia": round(self.base_dia, 6),
            "tip_dia": round(self.tip_dia, 6),
            "root_dia": round(self.root_dia, 6),
            "tooth_thickness_pitch": round(self.tooth_thickness_pitch, 6),
            "undercut": self.undercut,
            "pointed_tip": self.pointed_tip,
        }


@dataclass(frozen=True)
class ToothProfile:
    """One tooth's closed boss loop plus the gear's invariants and warnings."""

    invariants: GearInvariants
    segments: tuple[ProfileSeg, ...]
    warnings: tuple[str, ...]

    @property
    def spline_point_count(self) -> int:
        return sum(len(s.points) for s in self.segments if isinstance(s, SplineSeg))


def involute_inv(alpha: float) -> float:
    """The involute function inv(α) = tan α − α (α in radians)."""
    return math.tan(alpha) - alpha


def undercut_limit(pressure_angle_deg: float) -> float:
    """Minimum tooth count avoiding undercut for a full-depth rack: 2/sin²α."""
    a = math.radians(pressure_angle_deg)
    return 2.0 / (math.sin(a) ** 2)


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _arc_through(start: Point, end: Point, center: Point) -> ArcSeg:
    """Arc start→end about `center`, winding chosen for the minor arc."""
    ccw = _cross((start[0] - center[0], start[1] - center[1]),
                 (end[0] - center[0], end[1] - center[1])) > 0
    return ArcSeg(center=center, start=start, end=end, ccw=ccw)


def _flip(seg: ArcSeg | LineSeg) -> ArcSeg | LineSeg:
    """Reverse a segment's direction (arcs flip their winding)."""
    if isinstance(seg, ArcSeg):
        return ArcSeg(center=seg.center, start=seg.end, end=seg.start, ccw=not seg.ccw)
    return LineSeg(start=seg.end, end=seg.start)


def spur_gear_tooth(
    module: float,
    teeth: int,
    pressure_angle_deg: float = 20.0,
    n_points: int = 18,
    fillet_factor: float = 0.38,
) -> ToothProfile:
    """Generate one involute tooth boss profile (centered on +x) + invariants.

    The tooth is a closed loop, drawn CCW from the right root base:
    right root fillet → right involute flank (spline) → tip land arc →
    left involute flank (spline) → left root fillet → root base arc. Boss-
    extruding it on a root-diameter cylinder and circular-patterning it z
    times produces the full gear.
    """
    warnings: list[str] = []
    m, z = float(module), int(teeth)
    alpha = math.radians(pressure_angle_deg)
    rp = m * z / 2.0
    rb = rp * math.cos(alpha)
    ra = rp + m  # addendum = m
    rf = rp - 1.25 * m  # dedendum = 1.25 m
    r_fillet = fillet_factor * m

    undercut = z < undercut_limit(pressure_angle_deg) - 1e-9
    if undercut:
        warnings.append(
            f"gear: z={z} is below the undercut limit "
            f"{undercut_limit(pressure_angle_deg):.1f} for a {pressure_angle_deg:g}° "
            "pressure angle; the real generated flank would be undercut near the "
            "root (the twin models a clean fillet, not the trochoid)"
        )
    sub_base = rf < rb - 1e-9
    if sub_base:
        warnings.append(
            "gear: the root circle lies below the base circle, so the active "
            "flank's lower part is a fillet/trochoid, not an involute (exact "
            "shape is Windows-verified)"
        )

    # Involute roll-angle range: base/form circle up to the tip.
    t_tip = math.sqrt(max((ra / rb) ** 2 - 1.0, 0.0))
    r_form = max(rb, rf)
    t_form = math.sqrt(max((r_form / rb) ** 2 - 1.0, 0.0))
    # Half tooth angle at the pitch circle is π/(2z); the involute rolls by
    # inv(α) from the base circle to the pitch circle, so the flank is
    # rotated by β to place the pitch point at π/(2z).
    beta = math.pi / (2.0 * z) - involute_inv(alpha)

    def flank_point(t: float, side: int) -> Point:
        r = rb * math.sqrt(1.0 + t * t)
        phi = t - math.atan(t)
        ang = side * (beta + phi)
        return (r * math.cos(ang), r * math.sin(ang))

    ts = [t_form + (t_tip - t_form) * i / (n_points - 1) for i in range(n_points)]
    left_flank = [flank_point(t, +1) for t in ts]  # base → tip
    right_flank = [flank_point(t, -1) for t in ts]

    # Pointed tooth: the flanks meet before the tip (no tip land).
    tip_half_angle = beta + (t_tip - math.atan(t_tip))
    pointed = tip_half_angle <= 1e-6
    if pointed:
        warnings.append(
            f"gear: the tooth comes to a point before the tip (z={z}, module={m}); "
            "there is no tip land — reduce the addendum or increase z"
        )

    # Root region per side. When the root circle lies below the base circle
    # (the usual case) the active flank stops at the base circle F; below it
    # a short radial line drops to a small fillet of radius r_fillet ≈
    # 0.38·m that is tangent to that radial line AND to the root circle —
    # the standard drawn-gear root. When rf ≥ rb the flank already reaches
    # the root, and a single small tangent arc blends F into the root floor.
    # The EXACT hob trochoid is a Windows-verified refinement either way.
    def root_region(
        flank: list[Point], side: int
    ) -> tuple[list[ArcSeg | LineSeg], Point, float]:
        f = flank[0]
        theta = math.atan2(f[1], f[0])
        ux, uy = math.cos(theta), math.sin(theta)  # radial unit at F's angle
        if sub_base:
            # fillet tangent to the radial line and the root circle
            rho = math.sqrt(rf * rf + 2.0 * rf * r_fillet)
            px, py = (-uy, ux)  # +angle perpendicular (tooth-space side)
            perp = side  # left flank space is at larger angle
            c = (rho * ux + r_fillet * perp * px, rho * uy + r_fillet * perp * py)
            t_pt = (rho * ux, rho * uy)  # tangent on the radial line
            cn = math.hypot(*c) or 1.0
            b = (c[0] / cn * rf, c[1] / cn * rf)  # tangent on the root circle
            line = LineSeg(start=f, end=t_pt)
            arc = ArcSeg(center=c, start=t_pt, end=b,
                         ccw=_cross((t_pt[0] - c[0], t_pt[1] - c[1]),
                                    (b[0] - c[0], b[1] - c[1])) > 0)
            return [line, arc], b, r_fillet
        # rf ≥ rb: single tangent arc from F through its radial projection.
        b = (rf * ux, rf * uy)
        tx, ty = flank[1][0] - f[0], flank[1][1] - f[1]
        tlen = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / tlen, tx / tlen
        fbx, fby = f[0] - b[0], f[1] - b[1]
        denom = 2.0 * (nx * fbx + ny * fby)
        if abs(denom) < 1e-9:
            return [LineSeg(start=f, end=b)], b, 0.0
        s = -(fbx * fbx + fby * fby) / denom
        c = (f[0] + s * nx, f[1] + s * ny)
        arc = ArcSeg(center=c, start=f, end=b,
                     ccw=_cross((f[0] - c[0], f[1] - c[1]),
                                (b[0] - c[0], b[1] - c[1])) > 0)
        return [arc], b, abs(s)

    left_region, bl, rfil = root_region(left_flank, +1)
    right_region, br, _ = root_region(right_flank, -1)
    actual_fillet = rfil if rfil > 0 else r_fillet

    # Closed loop, walked once: right root (BR→FR) → right flank (FR→tip)
    # → tip land (tip_R→tip_L) → left flank (tip→FL) → left root (FL→BL)
    # → root base arc (BL→BR, under the tooth on the root cylinder).
    segments: list[ProfileSeg] = []
    for seg in reversed(right_region):  # BR → FR
        segments.append(_flip(seg))
    segments.append(SplineSeg(points=tuple(right_flank)))  # FR → tip_R
    if not pointed:
        segments.append(_arc_through(right_flank[-1], left_flank[-1], (0.0, 0.0)))
    segments.append(SplineSeg(points=tuple(reversed(left_flank))))  # tip_L → FL
    segments.extend(left_region)  # FL → BL
    segments.append(_arc_through(bl, br, (0.0, 0.0)))  # BL → BR

    # Circular tooth thickness at the pitch circle (exact by construction).
    s_pitch = 2.0 * rp * (math.pi / (2.0 * z))

    inv = GearInvariants(
        module=m,
        teeth=z,
        pressure_angle_deg=pressure_angle_deg,
        pitch_dia=2.0 * rp,
        base_dia=2.0 * rb,
        tip_dia=2.0 * ra,
        root_dia=2.0 * rf,
        tooth_thickness_pitch=s_pitch,
        fillet_radius=actual_fillet,
        undercut=undercut,
        pointed_tip=pointed,
        sub_base_flank=sub_base,
    )
    return ToothProfile(
        invariants=inv, segments=tuple(segments), warnings=tuple(warnings)
    )


def gear_center_distance(module: float, z1: int, z2: int) -> float:
    """Standard meshing center distance a = m·(z1 + z2)/2 (mm)."""
    return module * (z1 + z2) / 2.0


@dataclass(frozen=True)
class MeshResult:
    meshes: bool
    center_distance: float | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"meshes": self.meshes}
        if self.center_distance is not None:
            d["center_distance"] = round(self.center_distance, 6)
        if self.reasons:
            d["reasons"] = list(self.reasons)
        return d


def check_mesh(a: GearInvariants, b: GearInvariants, tol: float = 1e-6) -> MeshResult:
    """Two involute gears mesh iff equal module and pressure angle.

    Returns the standard center distance when they mesh. Mismatched
    modules or pressure angles are reported with the offending values.
    """
    reasons: list[str] = []
    if abs(a.module - b.module) > tol:
        reasons.append(f"module mismatch: {a.module} vs {b.module}")
    if abs(a.pressure_angle_deg - b.pressure_angle_deg) > tol:
        reasons.append(
            f"pressure-angle mismatch: {a.pressure_angle_deg}° vs {b.pressure_angle_deg}°"
        )
    if reasons:
        return MeshResult(meshes=False, center_distance=None, reasons=tuple(reasons))
    cd = gear_center_distance(a.module, a.teeth, b.teeth)
    return MeshResult(meshes=True, center_distance=cd, reasons=())


# --------------------------------------------------------------------------
# Internal ring gear (involute teeth cut inward into a rim)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RingGearInvariants:
    module: float
    teeth: int
    pressure_angle_deg: float
    pitch_dia: float
    base_dia: float
    tip_dia: float  # inner: m(z-2)
    root_dia: float  # outer: m(z+2.5)
    rim_outer_dia: float
    tooth_thickness_pitch: float

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "teeth": self.teeth,
            "pressure_angle_deg": self.pressure_angle_deg,
            "pitch_dia": round(self.pitch_dia, 6),
            "tip_dia": round(self.tip_dia, 6),
            "root_dia": round(self.root_dia, 6),
            "rim_outer_dia": round(self.rim_outer_dia, 6),
        }


@dataclass(frozen=True)
class RingToothSpace:
    """One tooth-space cut profile for an internal gear + invariants."""

    invariants: RingGearInvariants
    segments: tuple[ProfileSeg, ...]
    warnings: tuple[str, ...]


def ring_gear_tooth_space(
    module: float,
    teeth: int,
    rim_outer_dia: float,
    pressure_angle_deg: float = 20.0,
    n_points: int = 18,
) -> RingToothSpace:
    """One involute tooth-space profile to cut inward from an internal rim.

    For an internal gear the addendum/dedendum roles invert: the tooth tip
    is at the SMALLER radius m(z−2)/2 and the root at the LARGER radius
    m(z+2.5)/2. The space is bounded by involute flanks (base circle
    rb = m·z·cosα/2), an inner tip-land arc and an outer root arc. Cutting
    it from a rim and circular-patterning z times makes the ring gear.
    Exact internal-flank curvature is Windows-verified.
    """
    warnings: list[str] = []
    m, z = float(module), int(teeth)
    alpha = math.radians(pressure_angle_deg)
    rp = m * z / 2.0
    rb = rp * math.cos(alpha)
    ra_i = rp - m  # tip (inner)
    rf_i = rp + 1.25 * m  # root (outer)
    if rim_outer_dia / 2.0 <= rf_i + 1e-9:
        raise ValueError(
            f"ring_gear: rim outer radius {rim_outer_dia / 2:g} mm does not clear the "
            f"tooth root {rf_i:g} mm; the teeth would breach the rim"
        )
    if rim_outer_dia / 2.0 < rf_i + m:
        warnings.append(
            f"ring_gear: only {rim_outer_dia / 2 - rf_i:.2f} mm of rim over the tooth "
            f"root (< one module {m:g} mm); give a thicker rim for a real part"
        )

    r_lo = max(ra_i, rb)
    t_lo = math.sqrt(max((r_lo / rb) ** 2 - 1.0, 0.0))
    t_hi = math.sqrt(max((rf_i / rb) ** 2 - 1.0, 0.0))
    # The tooth SPACE half-angle at the pitch circle equals the mating
    # tooth half-thickness, π/(2z); flank rotated by β like the spur gear.
    beta = math.pi / (2.0 * z) - involute_inv(alpha)

    def flank_point(t: float, side: int) -> Point:
        r = rb * math.sqrt(1.0 + t * t)
        phi = t - math.atan(t)
        ang = side * (beta + phi)
        return (r * math.cos(ang), r * math.sin(ang))

    ts = [t_lo + (t_hi - t_lo) * i / (n_points - 1) for i in range(n_points)]
    left = [flank_point(t, +1) for t in ts]  # tip(inner) → root(outer)
    right = [flank_point(t, -1) for t in ts]

    # loop: inner tip arc (right→left) → left flank (in→out) → outer root
    # arc (left→right) → right flank (out→in).
    segments: list[ProfileSeg] = [
        _arc_through(right[0], left[0], (0.0, 0.0)),
        SplineSeg(points=tuple(left)),
        _arc_through(left[-1], right[-1], (0.0, 0.0)),
        SplineSeg(points=tuple(reversed(right))),
    ]
    inv = RingGearInvariants(
        module=m,
        teeth=z,
        pressure_angle_deg=pressure_angle_deg,
        pitch_dia=2.0 * rp,
        base_dia=2.0 * rb,
        tip_dia=2.0 * ra_i,
        root_dia=2.0 * rf_i,
        rim_outer_dia=rim_outer_dia,
        tooth_thickness_pitch=2.0 * rp * (math.pi / (2.0 * z)),
    )
    return RingToothSpace(
        invariants=inv, segments=tuple(segments), warnings=tuple(warnings)
    )


# --------------------------------------------------------------------------
# ISO-606 roller-chain sprocket
# --------------------------------------------------------------------------

# ISO 606 / BS 228 B-series chains: pitch p, roller diameter d1, inner
# width b1 (mm). Nominal max-material tooth forms are generated from these.
CHAIN_TABLE: dict[str, tuple[float, float, float]] = {
    "05B": (8.0, 5.0, 3.0),
    "06B": (9.525, 6.35, 5.72),
    "08B": (12.7, 8.51, 7.75),
    "10B": (15.875, 10.16, 9.65),
    "12B": (19.05, 12.07, 11.68),
    "16B": (25.4, 15.88, 17.02),
}


@dataclass(frozen=True)
class SprocketInvariants:
    chain: str
    teeth: int
    pitch: float  # chain pitch p, mm
    roller_dia: float  # d1
    pitch_dia: float  # p / sin(180/z)
    seating_radius: float  # ri
    tip_dia: float  # Da
    root_dia: float  # 2*(PD/2 - ri)

    def to_dict(self) -> dict[str, object]:
        return {
            "chain": self.chain,
            "teeth": self.teeth,
            "pitch": self.pitch,
            "roller_dia": self.roller_dia,
            "pitch_dia": round(self.pitch_dia, 6),
            "seating_radius": round(self.seating_radius, 6),
            "tip_dia": round(self.tip_dia, 6),
            "root_dia": round(self.root_dia, 6),
        }


@dataclass(frozen=True)
class SprocketTooth:
    invariants: SprocketInvariants
    segments: tuple[ProfileSeg, ...]
    warnings: tuple[str, ...]


def sprocket_tooth(chain: str, teeth: int, n_points: int = 12) -> SprocketTooth:
    """One ISO-606 sprocket tooth-gap profile (centered on +x), max-material.

    Construction per ISO 606: the roller seats in an arc of radius
    ri = 0.505·d1 centered on the pitch point; tooth flanks are arcs of
    radius re = 0.008·d1·(z²+180) tangent to the seating arcs, rising to a
    topping (tip) diameter Da = p·(0.6 + cot(180/z)). Half of the gap
    (seat arc + flank arc up to the tip) is generated and mirrored. Exact
    tolerance-band forms are Windows-verified; the twin checks p, PD, ri
    and Da.
    """
    warnings: list[str] = []
    if chain not in CHAIN_TABLE:
        raise ValueError(
            f"sprocket: unknown chain {chain!r}; known: {sorted(CHAIN_TABLE)}"
        )
    p, d1, _b1 = CHAIN_TABLE[chain]
    z = int(teeth)
    pd = p / math.sin(math.pi / z)  # pitch diameter
    r_pitch = pd / 2.0
    ri = 0.505 * d1  # roller seating radius
    re = 0.008 * d1 * (z * z + 180.0)  # tooth flank radius (max form)
    da = p * (0.6 + 1.0 / math.tan(math.pi / z))  # tip diameter (max)
    r_tip = da / 2.0
    r_root = r_pitch - ri  # seating-arc bottom radius from center

    # Seating arc: centered on the pitch point P=(r_pitch,0), radius ri.
    # It seats the roller; we take the half spanning from the gap centre
    # (angle 0, innermost point) up toward the flank.
    seat_center = (r_pitch, 0.0)
    # innermost seat point on +x axis:
    seat_bottom = (r_pitch - ri, 0.0)
    # Flank arc of radius re, tangent to the seating arc, sweeping out to
    # r_tip. Its center lies on the line through P and the seat/flank
    # tangent point, at distance re from that tangent point. We place the
    # tangent point at the seating-arc rim at the ISO seating angle.
    seat_angle = math.radians(140.0 - 90.0 / z)  # roller seating half-angle
    tan_pt = (
        seat_center[0] + ri * math.cos(math.pi - seat_angle),
        seat_center[1] + ri * math.sin(math.pi - seat_angle),
    )
    # flank arc center: outward along the seat radius from P through tan_pt
    ux = (tan_pt[0] - seat_center[0]) / ri
    uy = (tan_pt[1] - seat_center[1]) / ri
    flank_center = (tan_pt[0] + re * ux, tan_pt[1] + re * uy)
    # angle on the flank arc for the seat/flank tangent point:
    a0 = math.atan2(tan_pt[1] - flank_center[1], tan_pt[0] - flank_center[0])
    # find where the flank arc crosses r_tip
    hits = _line_circle_on_arc(flank_center, re, r_tip)
    if not hits:
        warnings.append(
            f"sprocket: flank arc (re={re:.2f}) does not reach the tip circle "
            f"(Da={da:.2f}); the tooth is truncated to the flank end"
        )
        tip_pt = (
            flank_center[0] + re * math.cos(a0 + math.radians(20)),
            flank_center[1] + re * math.sin(a0 + math.radians(20)),
        )
        a1 = math.atan2(tip_pt[1] - flank_center[1], tip_pt[0] - flank_center[0])
    else:
        tip_pt = max(hits, key=lambda q: math.atan2(q[1], q[0]))
        a1 = math.atan2(tip_pt[1] - flank_center[1], tip_pt[0] - flank_center[0])
    flank_pts = tuple(
        (
            flank_center[0] + re * math.cos(a0 + (a1 - a0) * i / (n_points - 1)),
            flank_center[1] + re * math.sin(a0 + (a1 - a0) * i / (n_points - 1)),
        )
        for i in range(n_points)
    )

    def mir(pt: Point) -> Point:
        return (pt[0], -pt[1])

    # loop: right seat/flank (mirror) then left seat/flank, joined by the
    # tip land and the gap-bottom seat point. Built as: left seat arc
    # (bottom→tan), left flank spline (tan→tip), tip land (tipL→tipR),
    # right flank spline, right seat arc back to bottom.
    tipR = mir(tip_pt)
    segments: list[ProfileSeg] = [
        _arc_through(seat_bottom, tan_pt, seat_center),
        SplineSeg(points=flank_pts),
        _arc_through(tip_pt, tipR, (0.0, 0.0)),
        SplineSeg(points=tuple(mir(q) for q in reversed(flank_pts))),
        _arc_through(mir(tan_pt), seat_bottom, seat_center),
    ]
    inv = SprocketInvariants(
        chain=chain,
        teeth=z,
        pitch=p,
        roller_dia=d1,
        pitch_dia=pd,
        seating_radius=ri,
        tip_dia=da,
        root_dia=2.0 * r_root,
    )
    return SprocketTooth(
        invariants=inv, segments=tuple(segments), warnings=tuple(warnings)
    )


def _line_circle_on_arc(center: Point, r_arc: float, r_circle: float) -> list[Point]:
    """Points at radius `r_circle` from origin lying on the arc circle."""
    return _circle_circle_pts((0.0, 0.0), r_circle, center, r_arc)


def _circle_circle_pts(c0: Point, r0: float, c1: Point, r1: float) -> list[Point]:
    dx, dy = c1[0] - c0[0], c1[1] - c0[1]
    d = math.hypot(dx, dy)
    if d < 1e-12 or d > r0 + r1 + 1e-9 or d < abs(r0 - r1) - 1e-9:
        return []
    a = (r0 * r0 - r1 * r1 + d * d) / (2 * d)
    h = math.sqrt(max(r0 * r0 - a * a, 0.0))
    xm, ym = c0[0] + a * dx / d, c0[1] + a * dy / d
    if h < 1e-12:
        return [(xm, ym)]
    return [(xm + h * dy / d, ym - h * dx / d), (xm - h * dy / d, ym + h * dx / d)]


# --------------------------------------------------------------------------
# Solid of revolution: envelope for the twin
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RevolveEnvelope:
    """Bounding cylinder/annulus of a profile revolved about an axis.

    ``axis`` is one of 'x','y','z' (a world axis through the origin). The
    profile points are (u, v) in the sketch plane; the radial coordinate
    is the distance from the axis, the axial coordinate runs along it.
    """

    axis: str
    max_radius: float  # mm
    min_radius: float  # mm (0 for a solid, >0 for a tube)
    axial_min: float
    axial_max: float
    angle_deg: float

    def to_dict(self) -> dict[str, object]:
        return {
            "axis": self.axis,
            "max_radius": round(self.max_radius, 6),
            "min_radius": round(self.min_radius, 6),
            "axial_extent": [round(self.axial_min, 6), round(self.axial_max, 6)],
            "angle_deg": self.angle_deg,
        }


def revolve_envelope(
    profile_points: list[Point],
    radial_index: int,
    axial_index: int,
    axis: str,
    angle_deg: float = 360.0,
) -> RevolveEnvelope:
    """Bounding annulus of a sketch profile revolved about an axis.

    ``radial_index``/``axial_index`` select which sketch coordinate is the
    radius (distance from the axis) and which is axial. Radii must be
    non-negative — a profile crossing the axis would self-intersect on
    revolution (that check lives in the twin, which raises).
    """
    radii = [abs(p[radial_index]) for p in profile_points]
    axials = [p[axial_index] for p in profile_points]
    return RevolveEnvelope(
        axis=axis,
        max_radius=max(radii),
        min_radius=min(radii),
        axial_min=min(axials),
        axial_max=max(axials),
        angle_deg=angle_deg,
    )


# --------------------------------------------------------------------------
# Cosmetic swept helix (visual threads)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HelixSpec:
    diameter: float
    pitch: float
    length: float
    revolutions: float
    right_handed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "diameter": self.diameter,
            "pitch": self.pitch,
            "length": self.length,
            "revolutions": round(self.revolutions, 6),
            "right_handed": self.right_handed,
        }


def helix_spec(
    diameter: float, pitch: float, length: float, right_handed: bool = True
) -> HelixSpec:
    """A cosmetic thread helix: revolutions = length / pitch.

    Cosmetic only — a swept triangular rib for visual threads, never a
    load-bearing thread form. The COM backend feeds these scalars to
    InsertHelix; the swept solid's validity is Windows-verified.
    """
    if pitch <= 0:
        raise ValueError("helix: pitch must be positive")
    return HelixSpec(
        diameter=diameter,
        pitch=pitch,
        length=length,
        revolutions=length / pitch,
        right_handed=right_handed,
    )
