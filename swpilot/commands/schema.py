"""Pydantic models for the SW-Pilot command schema (v0.5, accepts v0.1-v0.4).

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

SCHEMA_VERSION = "0.5"


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
    """Create a new, empty part document (and make it active).

    ``name`` is the session-local document name other commands refer to
    (insert_component, activate_document); defaults to Part1, Part2, ...
    """

    op: Literal["new_part"] = "new_part"
    name: str | None = None


class NewAssembly(_Cmd):
    """Create a new, empty assembly document (and make it active)."""

    op: Literal["new_assembly"] = "new_assembly"
    name: str | None = None


class ActivateDocument(_Cmd):
    """Switch the active document (part or assembly) by session name."""

    op: Literal["activate_document"] = "activate_document"
    name: str = Field(min_length=1)


class RotationStepSpec(_Sub):
    """A 90-degree-step rotation about a world axis."""

    axis: AxisName
    degrees: int

    @model_validator(mode="after")
    def _check_step(self) -> RotationStepSpec:
        if self.degrees % 90 != 0 or self.degrees % 360 == 0:
            raise ValueError(
                f"rotate: degrees must be a non-zero multiple of 90, got {self.degrees}"
            )
        return self


class InsertComponent(_Cmd):
    """Insert a component into the active assembly.

    Either ``part`` (a part document built in this session — it must be
    save_part-ed first, since SolidWorks inserts components from files)
    or ``file`` (an existing .SLDPRT path; give ``envelope`` [width,
    height, thickness] so its faces can be resolved for mates —
    declared, not verified; the envelope is centered in x/y with z from
    0 to thickness, matching a plate modeled on the front plane).
    ``rotate`` lists 90-degree steps about
    world axes, applied in order. The first inserted component is fixed
    automatically (SolidWorks convention).
    """

    op: Literal["insert_component"] = "insert_component"
    part: str | None = None
    file: str | None = None
    name: str | None = None
    at: tuple[Finite, Finite, Finite] = (0.0, 0.0, 0.0)
    rotate: list[RotationStepSpec] = Field(default_factory=list)
    fixed: bool = False
    envelope: tuple[PositiveMm, PositiveMm, PositiveMm] | None = None

    @model_validator(mode="after")
    def _check_source(self) -> InsertComponent:
        if (self.part is None) == (self.file is None):
            raise ValueError(
                "insert_component: give exactly one of 'part' (a session part "
                "document) or 'file' (an existing .SLDPRT path)"
            )
        if self.file is not None and not self.file.lower().endswith(".sldprt"):
            raise ValueError("insert_component: 'file' must be a .SLDPRT path")
        if self.part is not None and self.envelope is not None:
            raise ValueError(
                "insert_component: 'envelope' is only for external files; same-run "
                "parts carry their real geometry"
            )
        return self


class MateFace(_Sub):
    """A planar face of a component, by world-facing direction."""

    component: str
    facing: FacingName
    of_feature: str | None = None


class MateCylinder(_Sub):
    """A cylindrical face of a component (hole wall or shank).

    ``of_feature`` names the circular boss/cut; ``at`` (sketch
    coordinates) picks one circle when the feature has several.
    """

    component: str
    of_feature: str
    at: Point2D | None = None


MateEntity = MateFace | MateCylinder


class Mate(_Cmd):
    """Mate two component entities in the active assembly."""

    op: Literal["mate"] = "mate"
    type: Literal["coincident", "concentric", "distance", "parallel", "width"]
    a: MateEntity
    b: MateEntity
    value: PositiveMm | None = None  # distance mates only

    @model_validator(mode="after")
    def _check_value(self) -> Mate:
        if self.type == "distance" and self.value is None:
            raise ValueError("mate: distance mates require 'value' (mm)")
        if self.type != "distance" and self.value is not None:
            raise ValueError(f"mate: 'value' is only valid for distance mates, not {self.type}")
        return self


class SaveAssembly(_Cmd):
    """Save the active assembly. ``path`` must end in .sldasm."""

    op: Literal["save_assembly"] = "save_assembly"
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_extension(self) -> SaveAssembly:
        if not self.path.lower().endswith(".sldasm"):
            raise ValueError("save_assembly: path must end with .SLDASM")
        return self


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
# Curve primitives (v0.5): spline/arc/line sketch entities + revolve
# --------------------------------------------------------------------------


class DrawSpline(_Cmd):
    """Add an interpolating spline through points to the active sketch.

    Curve entities (spline/arc/line) form a curved profile and cannot
    share a sketch with rectangles/circles/slots. Emitted by the gear and
    sprocket macros; also usable directly for custom curved profiles.
    """

    op: Literal["draw_spline"] = "draw_spline"
    points: list[Point2D] = Field(min_length=2)
    kind: str | None = None


class DrawArc(_Cmd):
    """Add a circular arc (center + two endpoints) to the active sketch."""

    op: Literal["draw_arc"] = "draw_arc"
    center: Point2D
    start: Point2D
    end: Point2D
    ccw: bool = True
    kind: str | None = None


class DrawLine(_Cmd):
    """Add a straight line segment to the active sketch."""

    op: Literal["draw_line"] = "draw_line"
    start: Point2D
    end: Point2D
    kind: str | None = None

    @model_validator(mode="after")
    def _check_span(self) -> DrawLine:
        if self.start == self.end:
            raise ValueError("draw_line: start and end coincide")
        return self


class Revolve(_Cmd):
    """Revolve the active sketch profile about a world axis (through origin).

    The axis must lie in the sketch plane. ``angle`` < 360 makes a partial
    revolve. Consumes the active sketch (prismatic or curved).
    """

    op: Literal["revolve"] = "revolve"
    axis: AxisName
    angle: Annotated[float, Field(gt=0, le=360, allow_inf_nan=False)] = 360.0
    reverse: bool = False


class GearMeta(_Cmd):
    """Internal: tag the active part with involute-gear invariants.

    Emitted by the involute_spur_gear macro so assemblies can verify a
    mesh between two gear components. No COM calls; twin bookkeeping only.
    """

    op: Literal["gear_meta"] = "gear_meta"
    module: PositiveMm
    teeth: Annotated[int, Field(ge=4)]
    pressure_angle: Annotated[float, Field(gt=0, lt=45, allow_inf_nan=False)] = 20.0


# --------------------------------------------------------------------------
# Drawings (v0.4)
# --------------------------------------------------------------------------

PositiveInt = Annotated[int, BeforeValidator(_reject_bool), Field(ge=1)]
ScaleRatio = tuple[PositiveInt, PositiveInt]
ViewName = Literal["front", "top", "right"]


class CreateDrawing(_Cmd):
    """Create a drawing document of a saved part or assembly.

    ``of`` names a session document (default: the active one); it must
    have been saved first — SolidWorks drawing views reference the model
    by file path. ``scale`` (e.g. ``[1, 2]``) is the sheet scale; omit it
    to auto-pick the largest standard scale whose full standard-view
    layout fits the sheet. ``projection`` picks third-angle (SolidWorks
    default) or first-angle (ISO) placement. ``title``/``drawn_by``/
    ``date`` fill the title block via custom properties on the model.
    """

    op: Literal["create_drawing"] = "create_drawing"
    name: str | None = None
    of: str | None = None
    sheet: Literal["A4", "A3"] = "A3"
    scale: ScaleRatio | None = None
    projection: Literal["third", "first"] = "third"
    title: str | None = None
    drawn_by: str = "SW-Pilot"
    date: str = ""


class StandardViews(_Cmd):
    """Place the standard orthographic views on the active drawing.

    The front view anchors the layout; top and right are projected from
    it (SolidWorks applies the sheet's projection angle itself), so
    ``views`` must include "front".
    """

    op: Literal["standard_views"] = "standard_views"
    views: list[ViewName] = Field(default=["front", "top", "right"], min_length=1)

    @model_validator(mode="after")
    def _check_views(self) -> StandardViews:
        if "front" in self.views and len(set(self.views)) == len(self.views):
            return self
        if "front" not in self.views:
            raise ValueError(
                "standard_views: 'front' is required (it anchors the projected views)"
            )
        raise ValueError("standard_views: duplicate view names")


class IsometricView(_Cmd):
    """Place an isometric view in a sheet corner.

    ``scale`` defaults to one standard-series step smaller than the
    sheet scale.
    """

    op: Literal["isometric_view"] = "isometric_view"
    corner: Literal["top_right", "top_left", "bottom_right", "bottom_left"] = "top_right"
    scale: ScaleRatio | None = None


class SectionView(_Cmd):
    """Full section through the model center of an existing view.

    The cutting line runs vertically or horizontally through the parent
    view's center and the section is labeled A-A, B-B, ... in creation
    order — the essential view for hollow turned parts.
    """

    op: Literal["section_view"] = "section_view"
    parent: ViewName = "front"
    orientation: Literal["vertical", "horizontal"] = "vertical"


class SmartDimensions(_Cmd):
    """Dimension the governing features of the drawn model.

    Not an auto-dimension dump: emits the overall envelope, hole
    callouts in N x diameter form with datum position dimensions,
    pattern pitch, and fillet/chamfer notes — each attached to the view
    that shows it true-shape (missing views are skipped with warnings).
    """

    op: Literal["smart_dimensions"] = "smart_dimensions"


class SaveDrawing(_Cmd):
    """Save the active drawing. ``path`` must end in .slddrw."""

    op: Literal["save_drawing"] = "save_drawing"
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_extension(self) -> SaveDrawing:
        if not self.path.lower().endswith(".slddrw"):
            raise ValueError("save_drawing: path must end with .SLDDRW")
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


class BoltSpec(_Sub):
    """The bolt part for a bolt_circle: its shank and head boss features.

    The macro derives the seat orientation from the geometry (the head
    face that looks toward the shank).
    """

    part: str
    shank_feature: str = "Boss-Extrude1"
    head_feature: str = "Boss-Extrude2"


class HolesRef(_Sub):
    """A multi-circle hole feature of an assembly component."""

    component: str
    of_feature: str


class BoltCircle(_Cmd):
    """One bolt per hole: insert + concentric + head-seat coincident.

    Reads the hole positions straight from the referenced component's
    hole feature in the twin and expands to per-instance components and
    mates (each bolt independently fully mated). The bolt part must be
    built (and saved) in this session.
    """

    op: Literal["bolt_circle"] = "bolt_circle"
    bolt: BoltSpec
    holes: HolesRef
    seat: MateFace
    prefix: str = "bolt"


class Keyway(_Sub):
    """A rectangular keyway (DIN 6885 style) cut into a bore."""

    width: PositiveMm
    depth: PositiveMm  # radial depth past the bore wall


class InvoluteSpurGear(_Cmd):
    """A true involute spur gear: root cylinder + patterned tooth + bore.

    Generates a real involute tooth flank (parametric involute + tangent
    root fillet + tip land), boss-extrudes it on a root-diameter cylinder,
    and circular-patterns it ``teeth`` times. Standard metric geometry
    (addendum m, dedendum 1.25m). Optional hub boss and keyway.
    """

    op: Literal["involute_spur_gear"] = "involute_spur_gear"
    module: PositiveMm
    teeth: Annotated[int, Field(ge=4)]
    face_width: PositiveMm
    bore: PositiveMm  # bore diameter
    pressure_angle: Annotated[float, Field(gt=0, lt=45, allow_inf_nan=False)] = 20.0
    hub_diameter: PositiveMm | None = None
    hub_length: PositiveMm | None = None
    keyway: Keyway | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _check_hub(self) -> InvoluteSpurGear:
        if (self.hub_diameter is None) != (self.hub_length is None):
            raise ValueError(
                "involute_spur_gear: give both hub_diameter and hub_length, or neither"
            )
        if self.hub_diameter is not None and self.hub_diameter <= self.bore:
            raise ValueError(
                "involute_spur_gear: hub_diameter must exceed the bore diameter"
            )
        return self


class InternalRingGear(_Cmd):
    """An internal ring gear: involute teeth cut inward into a rim."""

    op: Literal["internal_ring_gear"] = "internal_ring_gear"
    module: PositiveMm
    teeth: Annotated[int, Field(ge=8)]
    face_width: PositiveMm
    rim_outer_diameter: PositiveMm
    pressure_angle: Annotated[float, Field(gt=0, lt=45, allow_inf_nan=False)] = 20.0
    name: str | None = None


class SprocketIso(_Cmd):
    """An ISO-606 roller-chain sprocket (real tooth profile)."""

    op: Literal["sprocket_iso"] = "sprocket_iso"
    chain: str  # e.g. "08B", "10B", "12B", "16B"
    teeth: Annotated[int, Field(ge=6)]
    face_width: PositiveMm
    bore: PositiveMm
    keyway: Keyway | None = None
    name: str | None = None


class HelixThread(_Cmd):
    """A cosmetic swept helical rib for visual threads (not load-bearing).

    Sweeps a small triangular rib along a helix on the last cylindrical
    boss. Cosmetic only — never a true thread form.
    """

    op: Literal["helix_thread"] = "helix_thread"
    diameter: PositiveMm  # nominal thread (cylinder) diameter
    pitch: PositiveMm
    length: PositiveMm
    right_handed: bool = True
    on_feature: str | None = None


class GearMeshCheck(_Cmd):
    """Verify two gear components in the active assembly mesh (no COM).

    Reports whether they mesh (equal module + pressure angle) and the
    standard center distance a = m·(z1+z2)/2; a mismatch fails validation.
    """

    op: Literal["gear_mesh_check"] = "gear_mesh_check"
    a: str  # component instance name
    b: str
    expected_center_distance: PositiveMm | None = None


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
    | NewAssembly
    | ActivateDocument
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
    | InsertComponent
    | Mate
    | SavePart
    | SaveAssembly
    | CreateDrawing
    | StandardViews
    | IsometricView
    | SectionView
    | SmartDimensions
    | SaveDrawing
    | DrawSpline
    | DrawArc
    | DrawLine
    | Revolve
    | HelixThread
    | GearMeta,
    Field(discriminator="op"),
]

MacroCommand = Annotated[
    CreatePlate
    | AddCornerHoles
    | Hole
    | BoltCircle
    | InvoluteSpurGear
    | InternalRingGear
    | SprocketIso
    | GearMeshCheck,
    Field(discriminator="op"),
]

Command = Annotated[
    NewPart
    | NewAssembly
    | ActivateDocument
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
    | InsertComponent
    | Mate
    | SavePart
    | SaveAssembly
    | CreateDrawing
    | StandardViews
    | IsometricView
    | SectionView
    | SmartDimensions
    | SaveDrawing
    | DrawSpline
    | DrawArc
    | DrawLine
    | Revolve
    | GearMeta
    | CreatePlate
    | AddCornerHoles
    | Hole
    | BoltCircle
    | InvoluteSpurGear
    | InternalRingGear
    | SprocketIso
    | HelixThread
    | GearMeshCheck,
    Field(discriminator="op"),
]

PRIMITIVE_OPS = frozenset(
    {
        "new_part",
        "new_assembly",
        "activate_document",
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
        "insert_component",
        "mate",
        "save_part",
        "save_assembly",
        "create_drawing",
        "standard_views",
        "isometric_view",
        "section_view",
        "smart_dimensions",
        "save_drawing",
        "draw_spline",
        "draw_arc",
        "draw_line",
        "revolve",
        "gear_meta",
        "helix_thread",
    }
)
MACRO_OPS = frozenset(
    {
        "create_plate",
        "add_corner_holes",
        "hole",
        "bolt_circle",
        "involute_spur_gear",
        "internal_ring_gear",
        "sprocket_iso",
        "gear_mesh_check",
    }
)


class CommandFile(BaseModel):
    """Top-level structure of a SW-Pilot command file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1", "0.2", "0.3", "0.4", "0.5"]
    commands: list[Command] = Field(min_length=1)
