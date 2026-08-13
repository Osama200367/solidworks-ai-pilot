"""Apply primitive commands to a :class:`SessionTracker`.

One dispatcher, two callers: macro expansion applies each emitted
primitive so later macros can query real session state (and so
``swpilot validate`` catches geometric errors with no backend at all),
and the executor applies each primitive before dispatching it to a
backend, capturing the resolution info (feature names, edge picks,
plane display names, component/mate data) the backend call needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swpilot.commands.schema import (
    ActivateDocument,
    Chamfer,
    CircularPattern,
    CreateAxis,
    CreatePlane,
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    DrawSlot,
    EdgeNearPoint,
    Extrude,
    Fillet,
    InsertComponent,
    LinearPattern,
    Mate,
    MateCylinder,
    MateEntity,
    MateFace,
    NewAssembly,
    NewPart,
    SaveAssembly,
    SavePart,
)
from swpilot.model.assembly import AssemblyTracker, ResolvedEntity
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import EdgeRec, ModelError
from swpilot.model.transforms import RotationStep, Transform, build_transform

PrimitiveT = (
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
)


@dataclass
class ComponentInsert:
    """Backend-facing data for one insert_component."""

    name: str
    path: str | None  # file the COM backend inserts (None: unsaved external? never)
    translation: tuple[float, float, float]
    rotation_row_major: list[float] | None  # None = identity
    fixed: bool
    external: bool = False  # from an existing file (needs OpenDoc6 preload)


@dataclass
class MateCall:
    """Backend-facing data for one mate."""

    name: str
    mate_type: str
    pick_a: tuple[float, float, float]
    pick_b: tuple[float, float, float]
    value: float | None


@dataclass
class ApplyResult:
    """What the session twin resolved for one primitive."""

    document: str | None = None
    doc_kind: str | None = None  # "part" | "assembly"
    feature_name: str | None = None
    plane_display: str | None = None
    axis_feature: str | None = None
    edges: list[EdgeRec] = field(default_factory=list)
    component: ComponentInsert | None = None
    mate: MateCall | None = None
    entities: list[ResolvedEntity] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def resolved_dict(self) -> dict[str, object] | None:
        """Selection info for the run report (None when trivial)."""
        out: dict[str, object] = {}
        if self.edges:
            out["edges"] = [e.to_dict() for e in self.edges]
        if self.entities:
            out["entities"] = [e.to_dict() for e in self.entities]
        return out or None


def _selector_args(
    cmd: Fillet | Chamfer,
) -> tuple[str | None, str | None, tuple[float, float, float] | None]:
    edges = cmd.edges
    if isinstance(edges, EdgeNearPoint):
        return None, None, edges.near_point
    return edges.select, edges.of_feature, None


def resolve_mate_entity(asm: AssemblyTracker, ref: MateEntity) -> ResolvedEntity:
    if isinstance(ref, MateFace):
        return asm.resolve_face(ref.component, ref.facing, ref.of_feature)
    assert isinstance(ref, MateCylinder)
    return asm.resolve_cylinder(ref.component, ref.of_feature, ref.at)


def apply_to_session(session: SessionTracker, cmd: PrimitiveT) -> ApplyResult:
    """Update/validate the session twin with one primitive; raises ModelError."""
    result = ApplyResult()

    if isinstance(cmd, NewPart):
        name, _ = session.new_part(cmd.name)
        result.document = name
        result.doc_kind = "part"
    elif isinstance(cmd, NewAssembly):
        name, _ = session.new_assembly(cmd.name)
        result.document = name
        result.doc_kind = "assembly"
    elif isinstance(cmd, ActivateDocument):
        doc = session.activate(cmd.name)
        result.document = cmd.name
        result.doc_kind = "assembly" if isinstance(doc, AssemblyTracker) else "part"
    elif isinstance(cmd, InsertComponent):
        asm = session.active_assembly("insert_component")
        if cmd.part is not None:
            part = session.part(cmd.part, "insert_component")
            saved = session.part_saved_path(cmd.part, "insert_component")
            source, envelope = cmd.part, None
        else:
            assert cmd.file is not None
            part, saved, source, envelope = None, cmd.file, cmd.file, cmd.envelope
        name = cmd.name or asm.next_instance_name(
            cmd.part if cmd.part is not None else "component"
        )
        transform = build_transform(
            [RotationStep(axis=r.axis, degrees=r.degrees) for r in cmd.rotate], cmd.at
        )
        rec = asm.insert_component(
            name=name,
            source=source,
            part=part,
            envelope=envelope,
            transform=transform,
            fixed=cmd.fixed,
            saved_path=saved,
        )
        rotation = None if transform.rotation == Transform().rotation else transform.to_row_major()
        result.component = ComponentInsert(
            name=name,
            path=rec.saved_path,
            translation=cmd.at,
            rotation_row_major=rotation,
            fixed=rec.fixed,
            external=cmd.file is not None,
        )
        result.feature_name = name
    elif isinstance(cmd, Mate):
        asm = session.active_assembly("mate")
        a = resolve_mate_entity(asm, cmd.a)
        b = resolve_mate_entity(asm, cmd.b)
        # Picks must be captured BEFORE solving: SolidWorks components sit
        # at their pre-mate positions when the selections execute (AddMate5
        # itself performs the move); the solver mutates entity coordinates.
        pick_a, pick_b = asm.mate_picks(a, b)
        mate_rec = asm.mate(cmd.type, a, b, cmd.value)
        result.mate = MateCall(
            name=mate_rec.name,
            mate_type=cmd.type,
            pick_a=pick_a,
            pick_b=pick_b,
            value=cmd.value,
        )
        result.entities = [a, b]
        result.feature_name = mate_rec.name
    elif isinstance(cmd, SaveAssembly):
        asm = session.active_assembly("save_assembly")
        asm.save_assembly(cmd.path)
    elif isinstance(cmd, CreatePlane):
        tracker = session.active_part("create_plane")
        tracker.create_plane(cmd.name, cmd.offset_from, cmd.distance)
        result.plane_display = tracker.plane_display_name(cmd.offset_from)
        result.feature_name = cmd.name
    elif isinstance(cmd, CreateAxis):
        tracker = session.active_part("create_axis")
        result.axis_feature = tracker.create_axis(cmd.axis)
        result.feature_name = result.axis_feature
    elif isinstance(cmd, CreateSketch):
        tracker = session.active_part("create_sketch")
        if cmd.on is not None:  # pragma: no cover - expansion resolves face refs
            raise ModelError(
                "create_sketch: face references must be resolved during macro "
                "expansion; this command should carry a plane name here"
            )
        tracker.create_sketch(cmd.plane)
        result.plane_display = tracker.plane_display_name(cmd.plane)
    elif isinstance(cmd, DrawRectangle):
        session.active_part("draw_rectangle").draw_rectangle(cmd.center, cmd.width, cmd.height)
    elif isinstance(cmd, DrawCircle):
        session.active_part("draw_circle").draw_circle(cmd.center, cmd.diameter)
    elif isinstance(cmd, DrawSlot):
        session.active_part("draw_slot").draw_slot(cmd.start, cmd.end, cmd.width)
    elif isinstance(cmd, Extrude):
        result.feature_name = (
            session.active_part("extrude").extrude(cmd.depth, cmd.reverse).name
        )
    elif isinstance(cmd, CutExtrude):
        result.feature_name = (
            session.active_part("cut_extrude")
            .cut_extrude(cmd.through_all, cmd.depth, cmd.reverse, cmd.draft_angle)
            .name
        )
    elif isinstance(cmd, Fillet):
        tracker = session.active_part("fillet")
        select, of_feature, near = _selector_args(cmd)
        feature, edges = tracker.fillet(cmd.radius, select, of_feature, near)
        result.feature_name = feature.name
        result.edges = edges
    elif isinstance(cmd, Chamfer):
        tracker = session.active_part("chamfer")
        select, of_feature, near = _selector_args(cmd)
        feature, edges = tracker.chamfer(cmd.distance, cmd.angle, select, of_feature, near)
        result.feature_name = feature.name
        result.edges = edges
    elif isinstance(cmd, LinearPattern):
        tracker = session.active_part("linear_pattern")
        d2 = (
            (cmd.direction2.direction, cmd.direction2.spacing, cmd.direction2.count)
            if cmd.direction2
            else None
        )
        result.feature_name = tracker.linear_pattern(
            cmd.features, cmd.direction, cmd.spacing, cmd.count, d2
        ).name
    elif isinstance(cmd, CircularPattern):
        tracker = session.active_part("circular_pattern")
        result.feature_name = tracker.circular_pattern(
            cmd.features, cmd.axis, cmd.count, cmd.total_angle, cmd.equal_spacing
        ).name
    elif isinstance(cmd, SavePart):
        session.active_part("save_part").save_part(cmd.path)
    else:  # pragma: no cover - schema and dispatcher must stay in sync
        raise ModelError(f"no session dispatch for op {cmd.op!r}")

    if result.document is None and session.active is not None:
        result.document = session.active
        doc = session.documents[session.active]
        result.doc_kind = "assembly" if isinstance(doc, AssemblyTracker) else "part"
    result.warnings = session.pop_warnings()
    return result
