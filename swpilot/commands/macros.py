"""Macro expansion: high-level commands become primitive sequences.

Expansion is stateful through a :class:`ModelTracker` that the loader
advances with every emitted primitive, so macros can query real model
state (the last boss's envelope, existing planes/axes, feature extents)
and everything they emit is fully concrete — visible via
``swpilot expand``. Expansion errors are validation-time errors: a bad
macro fails ``swpilot validate`` before any backend exists.
"""

from __future__ import annotations

import math

from swpilot.commands.schema import (
    AddCornerHoles,
    BoltCircle,
    CircularPattern,
    CreateAxis,
    CreatePlane,
    CreatePlate,
    CreateSketch,
    CutExtrude,
    DrawArc,
    DrawCircle,
    DrawLine,
    DrawRectangle,
    DrawSpline,
    Extrude,
    FaceRef,
    FacingName,
    GearMeshCheck,
    GearMeta,
    Hole,
    InsertComponent,
    InternalRingGear,
    InvoluteSpurGear,
    Keyway,
    LinearPattern,
    Mate,
    MateCylinder,
    MateFace,
    NewPart,
    RotationStepSpec,
    SprocketIso,
)
from swpilot.model import curves as cv
from swpilot.model import geometry as g
from swpilot.model.planes import AXIS_VECTORS, FAMILY_FOR_AXIS, PlaneFamily, Vec3
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import FeatureRec, ModelTracker
from swpilot.tolerances import EPS

# Emitted primitives (a subset of PrimitiveT; typed loosely to keep the
# expansion signatures simple).
Emitted = object


class MacroExpansionError(ValueError):
    """A macro is invalid in context (bad geometry, missing plate, ...)."""


def _last_boss(tracker: ModelTracker, op: str) -> FeatureRec:
    boss = tracker.last_boss()
    if boss is None:
        raise MacroExpansionError(
            f"{op}: no boss feature exists yet; create one first "
            "(e.g. create_plate, or a sketch + extrude)"
        )
    return boss


def _resolve_face(
    tracker: ModelTracker, face: FaceRef, op: str
) -> tuple[PlaneFamily, float, float]:
    """(plane family, position, outward sign) for a facing-style face ref."""
    axis = face.facing[1]  # "x" | "y" | "z"
    sign = 1.0 if face.facing[0] == "+" else -1.0
    if face.of_feature is not None:
        feature = tracker.feature(face.of_feature)
        if feature.kind != "boss":
            raise MacroExpansionError(
                f"{op}: face references need a boss feature, "
                f"{face.of_feature!r} is a {feature.kind}"
            )
    else:
        feature = _last_boss(tracker, op)
    mins, maxs = tracker.feature_aabb(feature.name)
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    position = maxs[idx] if sign > 0 else mins[idx]
    family = FAMILY_FOR_AXIS[axis]  # type: ignore[index]
    return family, position, sign


def _plane_for(
    tracker: ModelTracker, family: PlaneFamily, position: float
) -> tuple[str, list[Emitted]]:
    """Find or create a sketching plane at ``position`` on ``family``."""
    if abs(position) <= EPS:
        return family, []
    existing = tracker.find_plane_at(family, position)
    if existing is not None:
        return existing, []
    name = tracker.next_auto_plane_name()
    return name, [CreatePlane(name=name, offset_from=family, distance=position)]


# --------------------------------------------------------------------------
# create_plate
# --------------------------------------------------------------------------


def expand_create_plate(cmd: CreatePlate) -> list[Emitted]:
    return [
        NewPart(),
        CreateSketch(plane=cmd.plane),
        DrawRectangle(center=(0.0, 0.0), width=cmd.width, height=cmd.height),
        Extrude(depth=cmd.thickness),
    ]


# --------------------------------------------------------------------------
# add_corner_holes
# --------------------------------------------------------------------------


def expand_add_corner_holes(cmd: AddCornerHoles, tracker: ModelTracker) -> list[Emitted]:
    boss = _last_boss(tracker, "add_corner_holes")
    assert boss.sketch is not None
    rects = [e for e in boss.sketch.entities if isinstance(e, g.Rect)]
    if len(boss.sketch.entities) != 1 or len(rects) != 1:
        raise MacroExpansionError(
            "add_corner_holes: the last boss must be a single rectangle "
            "(e.g. from create_plate) so corner positions are well-defined"
        )
    rect = rects[0]
    r = cmd.diameter / 2.0
    # Thresholds include the shared EPS so a macro-accepted file can never
    # fail the tracker's strict-containment/disjointness checks later.
    if cmd.margin <= r + EPS:
        raise MacroExpansionError(
            f"add_corner_holes: margin ({cmd.margin} mm) must exceed the hole radius "
            f"({r} mm), otherwise the hole crosses or touches the plate edge "
            "(SolidWorks rejects zero-thickness geometry)."
        )
    if cmd.margin >= rect.width / 2.0 or cmd.margin >= rect.height / 2.0:
        raise MacroExpansionError(
            f"add_corner_holes: margin ({cmd.margin} mm) is too large for a "
            f"{rect.width}x{rect.height} mm plate; it must be less than half of "
            "each plate dimension."
        )
    for span, dim_name in ((rect.width, "width"), (rect.height, "height")):
        gap = span - 2.0 * cmd.margin
        if gap <= cmd.diameter + EPS:
            raise MacroExpansionError(
                f"add_corner_holes: holes would overlap or touch along the plate "
                f"{dim_name} ({span} mm): centers are {gap} mm apart but the hole "
                f"diameter is {cmd.diameter} mm."
            )
    x = rect.width / 2.0 - cmd.margin
    y = rect.height / 2.0 - cmd.margin
    corners = [
        (rect.cx - x, rect.cy - y),
        (rect.cx + x, rect.cy - y),
        (rect.cx - x, rect.cy + y),
        (rect.cx + x, rect.cy + y),
    ]
    return [
        CreateSketch(plane=boss.sketch.frame.name),
        *[DrawCircle(center=c, diameter=cmd.diameter) for c in corners],
        # Cut in the boss's own extrusion direction — a reversed boss has
        # its material on the -normal side of the sketch plane.
        CutExtrude(through_all=True, reverse=boss.reverse),
    ]


# --------------------------------------------------------------------------
# hole
# --------------------------------------------------------------------------


def _nearest_interval(
    intervals: list[tuple[float, float]], position: float
) -> tuple[float, float]:
    """The material interval closest to ``position`` (0 when inside one)."""

    def dist(iv: tuple[float, float]) -> float:
        lo, hi = iv
        if lo - EPS <= position <= hi + EPS:
            return 0.0
        return min(abs(position - lo), abs(position - hi))

    return min(intervals, key=dist)


def _hole_target(cmd: Hole, tracker: ModelTracker) -> tuple[PlaneFamily, float, bool]:
    """(family, plane position, cut_reverse) for the hole's entry surface."""
    if cmd.on is None:
        boss = _last_boss(tracker, "hole")
        assert boss.sketch is not None
        frame = boss.sketch.frame
        outward = boss.direction_sign
        position = frame.offset + outward * (boss.depth_mm or 0.0)
        # The cut must run against the face's outward normal, back into
        # the material. Plane normals equal +family axis, so cutting along
        # -normal means reverse=True exactly when the face looks along +normal.
        return frame.family, position, outward > 0
    if isinstance(cmd.on, str):
        frame = tracker.frame(cmd.on)
        intervals = tracker.material_intervals(frame.family)
        if not intervals:
            raise MacroExpansionError(
                f"hole: no material exists on plane family '{frame.family}' to drill into"
            )
        # Aim at the NEAREST material, not the envelope midpoint — with
        # disjoint bosses the envelope's center can lie in the gap and
        # point the drill at the far boss.
        lo, hi = _nearest_interval(intervals, frame.offset)
        return frame.family, frame.offset, frame.offset >= (lo + hi) / 2.0
    family, position, outward = _resolve_face(tracker, cmd.on, "hole")
    return family, position, outward > 0


def _available_depth(
    tracker: ModelTracker, family: PlaneFamily, position: float, reverse: bool
) -> float | None:
    """Material available below (reverse) / above the hole entry surface."""
    intervals = tracker.material_intervals(family)
    if not intervals:
        return None
    lo, hi = _nearest_interval(intervals, position)
    return position - lo if reverse else hi - position


def _check_hole_depth(
    kind: str, needed: float, available: float | None, position: float
) -> None:
    if available is None:
        return
    if available <= EPS:
        raise MacroExpansionError(
            f"hole: no material below the entry surface at {position} mm to drill into"
        )
    if needed >= available - EPS:
        raise MacroExpansionError(
            f"hole: the {kind} is {needed} mm deep but only {available} mm of "
            "material exists below the entry surface; the stepped hole would "
            "degenerate into a plain through-hole (SolidWorks then rejects the "
            "follow-up cut). Reduce the depth, pick another face, or use a "
            "thicker part."
        )


def expand_hole(cmd: Hole, tracker: ModelTracker) -> list[Emitted]:
    assert cmd.diameter is not None  # schema guarantees completeness
    family, position, reverse = _hole_target(cmd, tracker)
    available = _available_depth(tracker, family, position, reverse)
    plane_name, out = _plane_for(tracker, family, position)

    def sketch_circles(diameter: float) -> list[Emitted]:
        return [
            CreateSketch(plane=plane_name),
            *[DrawCircle(center=p, diameter=diameter) for p in cmd.at],
        ]

    if cmd.type == "counterbore":
        assert cmd.cb_diameter is not None and cmd.cb_depth is not None
        _check_hole_depth("counterbore", cmd.cb_depth, available, position)
        out += sketch_circles(cmd.cb_diameter)
        out.append(CutExtrude(depth=cmd.cb_depth, reverse=reverse))
    elif cmd.type == "countersink":
        assert cmd.cs_diameter is not None
        angle = cmd.effective_cs_angle
        # Drafted blind cut: the cone starts at cs_diameter on the surface
        # and necks down to the hole diameter at depth t.
        t = (cmd.cs_diameter - cmd.diameter) / (2.0 * math.tan(math.radians(angle / 2.0)))
        _check_hole_depth("countersink cone", t, available, position)
        out += sketch_circles(cmd.cs_diameter)
        out.append(CutExtrude(depth=t, reverse=reverse, draft_angle=angle / 2.0))
    out += sketch_circles(cmd.diameter)
    out.append(CutExtrude(through_all=True, reverse=reverse))
    return out


# --------------------------------------------------------------------------
# create_sketch on a face reference
# --------------------------------------------------------------------------


def expand_sketch_on_face(cmd: CreateSketch, tracker: ModelTracker) -> list[Emitted]:
    assert cmd.on is not None
    family, position, _outward = _resolve_face(tracker, cmd.on, "create_sketch")
    plane_name, out = _plane_for(tracker, family, position)
    out.append(CreateSketch(plane=plane_name))
    return out


# --------------------------------------------------------------------------
# bolt_circle
# --------------------------------------------------------------------------


def _flip_facing(facing: FacingName) -> FacingName:
    return ("-" if facing[0] == "+" else "+") + facing[1]  # type: ignore[return-value]


def _facing_vec(facing: FacingName) -> Vec3:
    sign = 1.0 if facing[0] == "+" else -1.0
    base = AXIS_VECTORS[facing[1]]  # type: ignore[index]
    return (base[0] * sign, base[1] * sign, base[2] * sign)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _rotation_steps_between(a: Vec3, b: Vec3) -> list[RotationStepSpec]:
    """90-degree steps rotating axis-aligned unit vector ``a`` onto ``b``."""
    if a == b:
        return []
    dot_ab = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    if dot_ab < -0.5:  # antiparallel: 180 about any perpendicular world axis
        for i, axis in enumerate("xyz"):
            if abs(a[i]) < 0.5:
                return [RotationStepSpec(axis=axis, degrees=180)]  # type: ignore[arg-type]
    # perpendicular: +90 about k where k x a = b (i.e. k = a x b)
    k = _cross(a, b)
    for i, axis in enumerate("xyz"):
        if abs(k[i]) > 0.5:
            return [RotationStepSpec(axis=axis, degrees=90 if k[i] > 0 else -90)]  # type: ignore[arg-type]
    raise MacroExpansionError("bolt_circle: cannot orient the bolt (internal)")


def expand_bolt_circle(cmd: BoltCircle, session: SessionTracker) -> list[Emitted]:
    asm = session.active_assembly("bolt_circle")
    holes_comp = asm.component(cmd.holes.component)
    if holes_comp.part is None:
        raise MacroExpansionError(
            "bolt_circle: the holes component must be a same-run part; external "
            "components carry no hole geometry"
        )
    feature = holes_comp.part.feature(cmd.holes.of_feature)
    if feature.sketch is None or feature.kind != "cut":
        raise MacroExpansionError(
            f"bolt_circle: {cmd.holes.of_feature!r} is not a hole (cut) feature"
        )
    assert feature.sketch is not None  # guarded by the kind check above
    circles = [e for e in feature.sketch.entities if isinstance(e, g.Circle)]
    if not circles:
        raise MacroExpansionError(
            f"bolt_circle: {cmd.holes.of_feature!r} contains no circular holes"
        )
    if len({round(c.diameter, 6) for c in circles}) > 1:
        raise MacroExpansionError(
            "bolt_circle: the hole feature mixes diameters; one bolt size cannot "
            "fit all of them"
        )

    bolt_part = session.part(cmd.bolt.part, "bolt_circle")
    if not bolt_part.saved_to:
        raise MacroExpansionError(
            f"bolt_circle: bolt part {cmd.bolt.part!r} has not been saved; add "
            "save_part before the assembly section"
        )
    shank = bolt_part.feature(cmd.bolt.shank_feature)
    head = bolt_part.feature(cmd.bolt.head_feature)
    for f, label in ((shank, "shank"), (head, "head")):
        if f.kind != "boss" or f.sketch is None:
            raise MacroExpansionError(f"bolt_circle: {label} feature {f.name!r} is not a boss")
    assert shank.sketch is not None and head.sketch is not None
    shank_circles = [e for e in shank.sketch.entities if isinstance(e, g.Circle)]
    if len(shank_circles) != 1:
        raise MacroExpansionError(
            f"bolt_circle: shank feature {shank.name!r} must be a single circle boss"
        )
    shank_r = shank_circles[0].r
    hole_r = circles[0].r
    if shank_r >= hole_r - EPS:
        raise MacroExpansionError(
            f"bolt_circle: shank diameter {2 * shank_r} mm does not clear the "
            f"{2 * hole_r} mm holes; a fastener needs clearance (e.g. Ø9 holes "
            "for M8 bolts)"
        )
    head_circles = [e for e in head.sketch.entities if isinstance(e, g.Circle)]
    head_r = max((c.r for c in head_circles), default=0.0)
    if head_r <= hole_r + EPS:
        raise MacroExpansionError(
            f"bolt_circle: head diameter {2 * head_r} mm does not bear on the "
            f"{2 * hole_r} mm holes — the bolt would fall through; use a larger "
            "head or smaller holes"
        )

    # Which way does the bolt point? Local "down" = head -> shank.
    n = shank.sketch.frame.normal
    shank_lo, shank_hi = bolt_part.feature_aabb(shank.name)
    head_lo, head_hi = bolt_part.feature_aabb(head.name)
    axis_i = max(range(3), key=lambda i: abs(n[i]))
    shank_mid = (shank_lo[axis_i] + shank_hi[axis_i]) / 2.0
    head_mid = (head_lo[axis_i] + head_hi[axis_i]) / 2.0
    sign = 1.0 if shank_mid > head_mid else -1.0
    down_local: Vec3 = tuple(sign if i == axis_i else 0.0 for i in range(3))  # type: ignore[assignment]

    seat_face = asm.resolve_face(cmd.seat.component, cmd.seat.facing, cmd.seat.of_feature)
    target_down = _facing_vec(_flip_facing(cmd.seat.facing))
    steps = _rotation_steps_between(down_local, target_down)
    head_facing = _flip_facing(cmd.seat.facing)

    # Insert with a small standoff along the seat normal: the seat mate then
    # performs a real closing move, and the pre-solve pick points are never
    # ambiguously coplanar.
    standoff = 2.0  # mm
    seat_sign = 1.0 if cmd.seat.facing[0] == "+" else -1.0
    out: list[Emitted] = []
    for i, c in enumerate(circles):
        world_center = holes_comp.transform.apply(
            feature.sketch.frame.to_world(c.cx, c.cy, 0.0)
        )
        at = list(world_center)
        at[seat_face.axis] = seat_face.position + seat_sign * standoff
        name = f"{cmd.prefix}_{i + 1}"
        out.append(
            InsertComponent(
                part=cmd.bolt.part,
                name=name,
                at=(at[0], at[1], at[2]),
                rotate=steps,
            )
        )
        # Static side first ('a'), bolt second ('b'): the solver moves the
        # freer component and documents "b moves to a".
        out.append(
            Mate(
                type="concentric",
                a=MateCylinder(
                    component=cmd.holes.component,
                    of_feature=cmd.holes.of_feature,
                    at=(c.cx, c.cy),
                ),
                b=MateCylinder(component=name, of_feature=shank.name),
            )
        )
        out.append(
            Mate(
                type="coincident",
                a=cmd.seat,
                b=MateFace(component=name, facing=head_facing, of_feature=head.name),
            )
        )
    return out


# --------------------------------------------------------------------------
# pattern prerequisites (auto-created reference axes)
# --------------------------------------------------------------------------


def expand_pattern_axes(
    cmd: LinearPattern | CircularPattern, tracker: ModelTracker
) -> list[Emitted] | None:
    """Prefix a pattern with create_axis for any missing reference axis.

    Returns None when all axes already exist (pattern passes through
    as a plain primitive).
    """
    if isinstance(cmd, LinearPattern):
        axes = {cmd.direction.lstrip("-")}
        if cmd.direction2 is not None:
            axes.add(cmd.direction2.direction.lstrip("-"))
    else:
        axes = {cmd.axis}
    missing = [a for a in sorted(axes) if a not in tracker.axes]
    if not missing:
        return None
    return [*(CreateAxis(axis=a) for a in missing), cmd]  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Curve macros (v0.5)
# --------------------------------------------------------------------------


def _draw_loop(segments: object, kind: str) -> list[Emitted]:
    """Emit draw_spline/arc/line primitives for a curve segment loop."""
    out: list[Emitted] = []
    for seg in segments:  # type: ignore[attr-defined]
        if isinstance(seg, cv.SplineSeg):
            out.append(DrawSpline(points=list(seg.points), kind=kind))
        elif isinstance(seg, cv.ArcSeg):
            out.append(
                DrawArc(center=seg.center, start=seg.start, end=seg.end,
                        ccw=seg.ccw, kind=kind)
            )
        else:
            out.append(DrawLine(start=seg.start, end=seg.end, kind=kind))
    return out


def _keyway_cut(keyway: Keyway, bore: float) -> list[Emitted]:
    """A rectangular keyway slot cut through, on top of the bore (front plane)."""
    # The keyway sits at the top of the bore: a rectangle spanning the key
    # width, from the bore center out to r + depth (so the outer edge is
    # exactly `depth` past the bore wall — the schema's "radial depth past
    # the bore wall"). Height = depth + r with the center at (r+depth)/2
    # puts the inner edge at the bore center and the outer at r + depth.
    r = bore / 2.0
    cy = (r + keyway.depth) / 2.0
    return [
        CreateSketch(plane="front"),
        DrawRectangle(center=(0.0, cy), width=keyway.width, height=keyway.depth + r),
        CutExtrude(through_all=True),
    ]


def expand_involute_spur_gear(cmd: InvoluteSpurGear) -> list[Emitted]:
    tp = cv.spur_gear_tooth(cmd.module, cmd.teeth, cmd.pressure_angle)
    inv = tp.invariants
    if cmd.bore >= inv.root_dia - 2.0 * cmd.module:
        raise MacroExpansionError(
            f"involute_spur_gear: bore Ø{cmd.bore} leaves less than one module of rim "
            f"under the root circle Ø{inv.root_dia:g}; use a smaller bore"
        )
    if inv.pointed_tip:
        raise MacroExpansionError(
            f"involute_spur_gear: z={cmd.teeth} at module {cmd.module} produces a "
            "pointed tooth (no tip land); increase the tooth count"
        )
    out: list[Emitted] = [NewPart(name=cmd.name)]
    # root-diameter cylinder
    out += [
        CreateSketch(plane="front"),
        DrawCircle(diameter=inv.root_dia),
        Extrude(depth=cmd.face_width),
    ]
    # one tooth boss from the involute profile
    out.append(CreateSketch(plane="front"))
    out += _draw_loop(tp.segments, "gear_tooth")
    out.append(Extrude(depth=cmd.face_width))
    # circular-pattern the tooth z times
    out += [
        CreateAxis(axis="z"),
        CircularPattern(features=["Boss-Extrude2"], axis="z", count=cmd.teeth),
    ]
    # bore
    out += [
        CreateSketch(plane="front"),
        DrawCircle(diameter=cmd.bore),
        CutExtrude(through_all=True),
    ]
    if cmd.keyway is not None:
        out += _keyway_cut(cmd.keyway, cmd.bore)
    if cmd.hub_diameter is not None and cmd.hub_length is not None:
        out += [
            CreatePlane(name="hub_base", offset_from="front", distance=cmd.face_width),
            CreateSketch(plane="hub_base"),
            DrawCircle(diameter=cmd.hub_diameter),
            Extrude(depth=cmd.hub_length),
        ]
    out.append(GearMeta(module=cmd.module, teeth=cmd.teeth, pressure_angle=cmd.pressure_angle))
    return out


def expand_internal_ring_gear(cmd: InternalRingGear) -> list[Emitted]:
    try:
        rg = cv.ring_gear_tooth_space(
            cmd.module, cmd.teeth, cmd.rim_outer_diameter, cmd.pressure_angle
        )
    except ValueError as exc:
        raise MacroExpansionError(f"internal_ring_gear: {exc}") from exc
    inv = rg.invariants
    out: list[Emitted] = [NewPart(name=cmd.name)]
    # rim tube: outer cylinder, then bore out the inner circle to the tip
    out += [
        CreateSketch(plane="front"),
        DrawCircle(diameter=cmd.rim_outer_diameter),
        Extrude(depth=cmd.face_width),
        CreateSketch(plane="front"),
        DrawCircle(diameter=inv.tip_dia),
        CutExtrude(through_all=True),
    ]
    # cut one tooth space, pattern z times
    out.append(CreateSketch(plane="front"))
    out += _draw_loop(rg.segments, "ring_space")
    out += [
        CutExtrude(through_all=True),
        CreateAxis(axis="z"),
        CircularPattern(features=["Cut-Extrude2"], axis="z", count=cmd.teeth),
    ]
    return out


def expand_sprocket_iso(cmd: SprocketIso) -> list[Emitted]:
    try:
        sp = cv.sprocket_tooth(cmd.chain, cmd.teeth)
    except ValueError as exc:
        raise MacroExpansionError(f"sprocket_iso: {exc}") from exc
    inv = sp.invariants
    if cmd.bore >= inv.root_dia - 2.0:
        raise MacroExpansionError(
            f"sprocket_iso: bore Ø{cmd.bore} does not leave a rim under the tooth-gap "
            f"root Ø{inv.root_dia:g}"
        )
    out: list[Emitted] = [NewPart(name=cmd.name)]
    # tip-diameter blank, then cut one tooth gap and pattern
    out += [
        CreateSketch(plane="front"),
        DrawCircle(diameter=inv.tip_dia),
        Extrude(depth=cmd.face_width),
    ]
    out.append(CreateSketch(plane="front"))
    out += _draw_loop(sp.segments, "sprocket_gap")
    out += [
        CutExtrude(through_all=True),
        CreateAxis(axis="z"),
        CircularPattern(features=["Cut-Extrude1"], axis="z", count=cmd.teeth),
        CreateSketch(plane="front"),
        DrawCircle(diameter=cmd.bore),
        CutExtrude(through_all=True),
    ]
    if cmd.keyway is not None:
        out += _keyway_cut(cmd.keyway, cmd.bore)
    return out


def expand_gear_mesh_check(cmd: GearMeshCheck, session: SessionTracker) -> list[Emitted]:
    asm = session.active_assembly("gear_mesh_check")
    ca, cb = asm.component(cmd.a), asm.component(cmd.b)
    if ca.part is None or cb.part is None:
        raise MacroExpansionError(
            "gear_mesh_check: both components must be same-run gear parts"
        )
    ga, gb = ca.part.gear, cb.part.gear
    if ga is None or gb is None:
        missing = cmd.a if ga is None else cmd.b
        raise MacroExpansionError(
            f"gear_mesh_check: component {missing!r} is not an involute_spur_gear"
        )
    result = cv.check_mesh(ga, gb)
    if not result.meshes:
        raise MacroExpansionError(
            f"gear_mesh_check: {cmd.a} and {cmd.b} do not mesh: "
            + "; ".join(result.reasons)
        )
    if (
        cmd.expected_center_distance is not None
        and result.center_distance is not None
        and abs(result.center_distance - cmd.expected_center_distance) > EPS
    ):
        raise MacroExpansionError(
            f"gear_mesh_check: center distance {result.center_distance:g} mm "
            f"≠ expected {cmd.expected_center_distance:g} mm"
        )
    return []  # pure validation, no emitted primitives


__all__ = [
    "MacroExpansionError",
    "expand_add_corner_holes",
    "expand_create_plate",
    "expand_gear_mesh_check",
    "expand_hole",
    "expand_internal_ring_gear",
    "expand_involute_spur_gear",
    "expand_pattern_axes",
    "expand_sketch_on_face",
    "expand_sprocket_iso",
]
