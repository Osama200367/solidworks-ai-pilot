"""Apply primitive commands to a :class:`ModelTracker`.

One dispatcher, two callers: macro expansion applies each emitted
primitive so later macros can query real model state (and so
``swpilot validate`` catches geometric errors with no backend at all),
and the executor applies each primitive before dispatching it to a
backend, capturing the resolution info (feature names, edge picks,
plane display names) the backend call needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swpilot.commands.schema import (
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
    LinearPattern,
    NewPart,
    SavePart,
)
from swpilot.model.tracker import EdgeRec, ModelError, ModelTracker

PrimitiveT = (
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
)


@dataclass
class ApplyResult:
    """What the tracker resolved for one primitive."""

    feature_name: str | None = None
    plane_display: str | None = None
    axis_feature: str | None = None
    edges: list[EdgeRec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def resolved_dict(self) -> dict[str, object] | None:
        """Selection info for the run report (None when trivial)."""
        if not self.edges:
            return None
        return {"edges": [e.to_dict() for e in self.edges]}


def _selector_args(
    cmd: Fillet | Chamfer,
) -> tuple[str | None, str | None, tuple[float, float, float] | None]:
    edges = cmd.edges
    if isinstance(edges, EdgeNearPoint):
        return None, None, edges.near_point
    return edges.select, edges.of_feature, None


def apply_to_tracker(tracker: ModelTracker, cmd: PrimitiveT) -> ApplyResult:
    """Update/validate the tracker with one primitive; raises ModelError."""
    result = ApplyResult()
    if isinstance(cmd, NewPart):
        tracker.new_part()
    elif isinstance(cmd, CreatePlane):
        tracker.create_plane(cmd.name, cmd.offset_from, cmd.distance)
        result.plane_display = tracker.plane_display_name(cmd.offset_from)
        result.feature_name = cmd.name
    elif isinstance(cmd, CreateAxis):
        result.axis_feature = tracker.create_axis(cmd.axis)
        result.feature_name = result.axis_feature
    elif isinstance(cmd, CreateSketch):
        if cmd.on is not None:  # pragma: no cover - expansion resolves face refs
            raise ModelError(
                "create_sketch: face references must be resolved during macro "
                "expansion; this command should carry a plane name here"
            )
        tracker.create_sketch(cmd.plane)
        result.plane_display = tracker.plane_display_name(cmd.plane)
    elif isinstance(cmd, DrawRectangle):
        tracker.draw_rectangle(cmd.center, cmd.width, cmd.height)
    elif isinstance(cmd, DrawCircle):
        tracker.draw_circle(cmd.center, cmd.diameter)
    elif isinstance(cmd, DrawSlot):
        tracker.draw_slot(cmd.start, cmd.end, cmd.width)
    elif isinstance(cmd, Extrude):
        result.feature_name = tracker.extrude(cmd.depth, cmd.reverse).name
    elif isinstance(cmd, CutExtrude):
        result.feature_name = tracker.cut_extrude(
            cmd.through_all, cmd.depth, cmd.reverse, cmd.draft_angle
        ).name
    elif isinstance(cmd, Fillet):
        select, of_feature, near = _selector_args(cmd)
        feature, edges = tracker.fillet(cmd.radius, select, of_feature, near)
        result.feature_name = feature.name
        result.edges = edges
    elif isinstance(cmd, Chamfer):
        select, of_feature, near = _selector_args(cmd)
        feature, edges = tracker.chamfer(cmd.distance, cmd.angle, select, of_feature, near)
        result.feature_name = feature.name
        result.edges = edges
    elif isinstance(cmd, LinearPattern):
        d2 = (
            (cmd.direction2.direction, cmd.direction2.spacing, cmd.direction2.count)
            if cmd.direction2
            else None
        )
        result.feature_name = tracker.linear_pattern(
            cmd.features, cmd.direction, cmd.spacing, cmd.count, d2
        ).name
    elif isinstance(cmd, CircularPattern):
        result.feature_name = tracker.circular_pattern(
            cmd.features, cmd.axis, cmd.count, cmd.total_angle, cmd.equal_spacing
        ).name
    elif isinstance(cmd, SavePart):
        tracker.save_part(cmd.path)
    else:  # pragma: no cover - schema and dispatcher must stay in sync
        raise ModelError(f"no tracker dispatch for op {cmd.op!r}")
    result.warnings = tracker.pop_warnings()
    return result
