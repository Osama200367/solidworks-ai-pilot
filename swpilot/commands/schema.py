"""Pydantic models for the SW-Pilot command schema (v0.2, accepts v0.1).

Two tiers share one discriminated union keyed on ``op``:

* primitives — map 1:1 onto backend operations (and thence COM calls)
* macros — expand into primitives before execution (see ``macros.py``)

All lengths are millimeters, angles in degrees. NaN/inf and boolean
"numbers" are rejected everywhere.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from swpilot.model.presets import FASTENER_PRESETS, preset_names

SCHEMA_VERSION = "0.2"


def _reject_bool(v: object) -> object:
    # bool is a subclass of int, so pydantic's lax mode would otherwise
    # coerce JSON true/false to 1.0/0.0 mm — never a dimension the user meant.
    if isinstance(v, bool):
        raise ValueError("booleans are not valid numbers")
    return v


# A finite float; pydantic allows inf/nan (and bools) by default, which we
# never want for geometry.
Finite = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]
PositiveMm = Annotated[float, BeforeValidator(_reject_bool), Field(gt=0, allow_inf_nan=False)]
Point2D = tuple[Finite, Finite]
Point3D = tuple[Finite, Finite, Finite]

StandardPlane = Literal["front", "top", "right"]
AxisName = Literal["x", "y", "z"]
DirectionName = Literal["x", "y", "z", "-x", "-y", "-z"]
FacingName = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Sub(BaseModel):
    """Non-command sub-object (selectors, nested specs)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Selectors and references
# --------------------------------------------------------------------------


class EdgeSelect(_Sub):
    """Named edge group of a feature.

    ``vertical_corners`` — lateral corner edges of a rectangular boss;
    ``top_loop``/``bottom_loop`` — cap perimeter (or cut rim) away
    from / on the sketch plane; ``all`` — every edge of the feature.
    ``of_feature`` defaults to the most recent feature that still has
    selectable edges.
    """

    select: Literal["vertical_corners", "top_loop", "bottom_loop", "all"]
    of_feature: str | None = None


class EdgeNearPoint(_Sub):
    """Escape hatch: the unconsumed edge nearest a world point (mm)."""

    near_point: Point3D


EdgeSelector = EdgeSelect | EdgeNearPoint


class FaceRef(_Sub):
    """A planar face by outward direction: e.g. facing '+z' of a feature.

    ``of_feature`` defaults to the last boss. SW-Pilot sketches on an
    offset reference plane at the face's position (auto-created and
    reused), because offset planes inherit their base plane's sketch
    axes deterministically while raw face sketches do not.
    """

    facing: FacingName
    of_feature: str | None = None


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


class NewPart(_Cmd):
    """Create a new, empty part document."""

    op: Literal["new_part"] = "new_part"


class CreatePlane(_Cmd):
    """Reference plane offset from a standard plane or an earlier plane."""

    op: Literal["create_plane"] = "create_plane"
    name: str = Field(min_length=1)
    offset_from: str = "front"
    distance: Finite  # signed, mm, along the base plane's normal


class CreateAxis(_Cmd):
    """Reference axis along a world axis through the origin.

    Built by intersecting two standard planes; required by pattern
    commands (macro expansion inserts it automatically when missing).
    """

    op: Literal["create_axis"] = "create_axis"
    axis: AxisName


class CreateSketch(_Cmd):
    """Open a sketch on a plane (standard, created) or on a planar face."""

    op: Literal["create_sketch"] = "create_sketch"
    plane: str = "front"
    on: FaceRef | None = None  # wins over `plane` when given

    @model_validator(mode="after")
    def _check_target(self) -> CreateSketch:
        # Only a non-default plane conflicts with 'on': model_dump() echoes
        # the default plane alongside 'on', and that dump must re-validate
        # (round-tripping matters for LLM repair loops and `expand` output).
        if self.on is not None and "plane" in self.model_fields_set and self.plane != "front":
            raise ValueError("create_sketch: give either 'plane' or 'on', not both")
        return self


class DrawRectangle(_Cmd):
    """Draw a center rectangle in the active sketch."""

    op: Literal["draw_rectangle"] = "draw_rectangle"
    center: Point2D = (0.0, 0.0)
    width: PositiveMm
    height: PositiveMm


class DrawCircle(_Cmd):
    """Draw a circle in the active sketch."""

    op: Literal["draw_circle"] = "draw_circle"
    center: Point2D = (0.0, 0.0)
    diameter: PositiveMm


class DrawSlot(_Cmd):
    """Draw a straight slot (stadium) in the active sketch."""

    op: Literal["draw_slot"] = "draw_slot"
    start: Point2D
    end: Point2D
    width: PositiveMm

    @model_validator(mode="after")
    def _check_span(self) -> DrawSlot:
        if self.start == self.end:
            raise ValueError("draw_slot: start and end coincide; use draw_circle instead")
        return self


class Extrude(_Cmd):
    """Blind boss-extrude the active sketch by ``depth`` mm.

    ``reverse`` extrudes against the sketch plane's normal.
    """

    op: Literal["extrude"] = "extrude"
    depth: PositiveMm
    reverse: bool = False


class CutExtrude(_Cmd):
    """Cut-extrude the active sketch: through-all (default) or blind.

    Giving ``depth`` implies a blind cut, so ``through_all`` may be
    omitted in that case. ``reverse`` cuts against the plane normal.
    ``draft_angle`` (degrees, blind cuts only) tapers the cut — used by
    the hole macro to produce countersink cones.
    """

    op: Literal["cut_extrude"] = "cut_extrude"
    through_all: bool = True
    depth: PositiveMm | None = None
    reverse: bool = False
    draft_angle: Annotated[float, Field(gt=0, lt=90, allow_inf_nan=False)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_through_all(cls, data: object) -> object:
        # An explicit "depth": null means the same as omitting depth, so it
        # must not suppress the through-all default — test the value, not
        # key presence.
        if isinstance(data, dict) and data.get("depth") is not None and "through_all" not in data:
            data = {**data, "through_all": False}
        return data

    @model_validator(mode="after")
    def _check_end_condition(self) -> CutExtrude:
        if self.through_all and self.depth is not None:
            raise ValueError(
                "cut_extrude: 'through_all' and 'depth' are mutually exclusive; "
                "omit 'through_all' (or set it to false) when giving a depth"
            )
        if not self.through_all and self.depth is None:
            raise ValueError("cut_extrude: a blind cut (through_all=false) requires 'depth'")
        if self.draft_angle is not None and self.through_all:
            raise ValueError("cut_extrude: draft_angle requires a blind cut (give 'depth')")
        return self


class Fillet(_Cmd):
    """Constant-radius fillet on selected edges."""

    op: Literal["fillet"] = "fillet"
    radius: PositiveMm
    edges: EdgeSelector


class Chamfer(_Cmd):
    """Distance-angle chamfer on selected edges."""

    op: Literal["chamfer"] = "chamfer"
    distance: PositiveMm
    angle: Annotated[float, Field(gt=0, lt=90, allow_inf_nan=False)] = 45.0
    edges: EdgeSelector


class Direction2(_Sub):
    direction: DirectionName
    spacing: PositiveMm
    count: Annotated[int, Field(ge=2)]


class LinearPattern(_Cmd):
    """Linear pattern of existing boss/cut features along world axes."""

    op: Literal["linear_pattern"] = "linear_pattern"
    features: list[str] = Field(min_length=1)
    direction: DirectionName
    spacing: PositiveMm
    count: Annotated[int, Field(ge=2)]
    direction2: Direction2 | None = None


class CircularPattern(_Cmd):
    """Circular pattern of existing boss/cut features about a world axis."""

    op: Literal["circular_pattern"] = "circular_pattern"
    features: list[str] = Field(min_length=1)
    axis: AxisName
    count: Annotated[int, Field(ge=2)]
    total_angle: Annotated[float, Field(gt=0, le=360, allow_inf_nan=False)] = 360.0
    equal_spacing: bool = True


class SavePart(_Cmd):
    """Save the part. ``path`` must end in .sldprt (case-insensitive)."""

    op: Literal["save_part"] = "save_part"
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_extension(self) -> SavePart:
        if not self.path.lower().endswith(".sldprt"):
            raise ValueError("save_part: path must end with .SLDPRT")
        return self


# --------------------------------------------------------------------------
# Macros
# --------------------------------------------------------------------------


class CreatePlate(_Cmd):
    """Rectangular plate: new part + centered base sketch + blind extrude."""

    op: Literal["create_plate"] = "create_plate"
    width: PositiveMm
    height: PositiveMm
    thickness: PositiveMm
    plane: StandardPlane = "front"


class AddCornerHoles(_Cmd):
    """Four through-holes, one per corner of the last rectangular boss.

    ``margin`` is the distance from each pair of adjacent edges to the
    hole center.
    """

    op: Literal["add_corner_holes"] = "add_corner_holes"
    diameter: PositiveMm
    margin: PositiveMm


class Hole(_Cmd):
    """One or more holes: simple, counterbored, or countersunk.

    Holes are drilled from a face/plane (default: the top face of the
    last boss) through everything below. Counterbores/countersinks are
    built pragmatically from composed cuts — the countersink cone is a
    drafted blind cut. ``standard`` (e.g. "M6") fills any dimension not
    given explicitly from a nominal metric preset table; explicit fields
    always win.
    """

    op: Literal["hole"] = "hole"
    at: list[Point2D] = Field(min_length=1)
    type: Literal["simple", "counterbore", "countersink"] = "simple"
    standard: str | None = None
    diameter: PositiveMm | None = None
    cb_diameter: PositiveMm | None = None
    cb_depth: PositiveMm | None = None
    cs_diameter: PositiveMm | None = None
    cs_angle: Annotated[float, Field(gt=0, lt=180, allow_inf_nan=False)] | None = None
    on: str | FaceRef | None = None  # plane name, or a face reference

    @model_validator(mode="before")
    @classmethod
    def _apply_standard(cls, data: object) -> object:
        if not isinstance(data, dict) or not data.get("standard"):
            return data
        preset = FASTENER_PRESETS.get(str(data["standard"]))
        if preset is None:
            raise ValueError(
                f"hole: unknown standard {data['standard']!r}; known: {preset_names()}"
            )
        merged = dict(data)

        def fill(key: str, value: float) -> None:
            # An explicit JSON null means "not given" (generated JSON often
            # emits every key), so it must not defeat the preset.
            if merged.get(key) is None:
                merged[key] = value

        fill("diameter", preset.clearance_diameter)
        if merged.get("type") == "counterbore":
            fill("cb_diameter", preset.cb_diameter)
            fill("cb_depth", preset.cb_depth)
        if merged.get("type") == "countersink":
            fill("cs_diameter", preset.cs_diameter)
            fill("cs_angle", preset.cs_angle)
        return merged

    @model_validator(mode="after")
    def _check_completeness(self) -> Hole:
        if self.diameter is None:
            raise ValueError("hole: 'diameter' is required (directly or via 'standard')")
        if self.type == "counterbore":
            if self.cb_diameter is None or self.cb_depth is None:
                raise ValueError(
                    "hole: counterbore needs 'cb_diameter' and 'cb_depth' "
                    "(directly or via 'standard')"
                )
            if self.cb_diameter <= self.diameter:
                raise ValueError(
                    f"hole: cb_diameter ({self.cb_diameter}) must exceed the hole "
                    f"diameter ({self.diameter})"
                )
        elif self.cb_diameter is not None or self.cb_depth is not None:
            raise ValueError("hole: cb_* fields are only valid with type=counterbore")
        if self.type == "countersink":
            if self.cs_diameter is None:
                raise ValueError(
                    "hole: countersink needs 'cs_diameter' (directly or via 'standard')"
                )
            if self.cs_diameter <= self.diameter:
                raise ValueError(
                    f"hole: cs_diameter ({self.cs_diameter}) must exceed the hole "
                    f"diameter ({self.diameter})"
                )
        elif self.cs_diameter is not None or self.cs_angle is not None:
            raise ValueError("hole: cs_* fields are only valid with type=countersink")
        return self

    @property
    def effective_cs_angle(self) -> float:
        return self.cs_angle if self.cs_angle is not None else 90.0


PrimitiveCommand = Annotated[
    NewPart
    | CreatePlane
    | CreateAxis
    | CreateSketch
    | DrawRectangle
    | DrawCircle
    | DrawSlot
    | Extrude
    | CutExtrude
    | Fillet
    | Chamfer
    | LinearPattern
    | CircularPattern
    | SavePart,
    Field(discriminator="op"),
]

MacroCommand = Annotated[
    CreatePlate | AddCornerHoles | Hole, Field(discriminator="op")
]

Command = Annotated[
    NewPart
    | CreatePlane
    | CreateAxis
    | CreateSketch
    | DrawRectangle
    | DrawCircle
    | DrawSlot
    | Extrude
    | CutExtrude
    | Fillet
    | Chamfer
    | LinearPattern
    | CircularPattern
    | SavePart
    | CreatePlate
    | AddCornerHoles
    | Hole,
    Field(discriminator="op"),
]

PRIMITIVE_OPS = frozenset(
    {
        "new_part",
        "create_plane",
        "create_axis",
        "create_sketch",
        "draw_rectangle",
        "draw_circle",
        "draw_slot",
        "extrude",
        "cut_extrude",
        "fillet",
        "chamfer",
        "linear_pattern",
        "circular_pattern",
        "save_part",
    }
)
MACRO_OPS = frozenset({"create_plate", "add_corner_holes", "hole"})


class CommandFile(BaseModel):
    """Top-level structure of a SW-Pilot command file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1", "0.2"]
    commands: list[Command] = Field(min_length=1)
