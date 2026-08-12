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
    CircularPattern,
    CreateAxis,
    CreatePlane,
    CreatePlate,
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    Extrude,
    FaceRef,
    Hole,
    LinearPattern,
    NewPart,
)
from swpilot.model import geometry as g
from swpilot.model.planes import FAMILY_FOR_AXIS, PlaneFamily
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


def expand_create_plate(cmd: CreatePlate, tracker: ModelTracker) -> list[Emitted]:
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
        CutExtrude(through_all=True),
    ]


# --------------------------------------------------------------------------
# hole
# --------------------------------------------------------------------------


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
        interval = tracker.material_interval(frame.family)
        if interval is None:
            raise MacroExpansionError(
                f"hole: no material exists on plane family '{frame.family}' to drill into"
            )
        center = (interval[0] + interval[1]) / 2.0
        return frame.family, frame.offset, frame.offset >= center
    family, position, outward = _resolve_face(tracker, cmd.on, "hole")
    return family, position, outward > 0


def expand_hole(cmd: Hole, tracker: ModelTracker) -> list[Emitted]:
    assert cmd.diameter is not None  # schema guarantees completeness
    family, position, reverse = _hole_target(cmd, tracker)
    plane_name, out = _plane_for(tracker, family, position)

    def sketch_circles(diameter: float) -> list[Emitted]:
        return [
            CreateSketch(plane=plane_name),
            *[DrawCircle(center=p, diameter=diameter) for p in cmd.at],
        ]

    if cmd.type == "counterbore":
        assert cmd.cb_diameter is not None and cmd.cb_depth is not None
        out += sketch_circles(cmd.cb_diameter)
        out.append(CutExtrude(depth=cmd.cb_depth, reverse=reverse))
    elif cmd.type == "countersink":
        assert cmd.cs_diameter is not None
        angle = cmd.effective_cs_angle
        # Drafted blind cut: the cone starts at cs_diameter on the surface
        # and necks down to the hole diameter at depth t.
        t = (cmd.cs_diameter - cmd.diameter) / (2.0 * math.tan(math.radians(angle / 2.0)))
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


__all__ = [
    "MacroExpansionError",
    "expand_add_corner_holes",
    "expand_create_plate",
    "expand_hole",
    "expand_pattern_axes",
    "expand_sketch_on_face",
]
