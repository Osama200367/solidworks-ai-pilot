# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Executor: tracker-validated dispatch of expanded commands to a backend.

For every command, in order: the shared :class:`ModelTracker` validates
it and resolves selections (raising before the backend is touched —
also on Windows, where this means bad files fail before any COM call);
then the backend executes/logs the shared call plan. Fail-fast: the
first error stops execution; remaining commands are reported as skipped.
The report attributes every logged COM call and every resolved selection
to the command that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import swpilot
from swpilot.backends.base import Backend, BackendError
from swpilot.commands.loader import ExpandedCommand
from swpilot.commands.schema import (
    ActivateDocument,
    Chamfer,
    CircularPattern,
    CreateAxis,
    CreateDrawing,
    CreatePlane,
    CreateSketch,
    CutExtrude,
    DrawArc,
    DrawCircle,
    DrawLine,
    DrawRectangle,
    DrawSlot,
    DrawSpline,
    Extrude,
    Fillet,
    GearMeta,
    HelixThread,
    InsertComponent,
    IsometricView,
    LinearPattern,
    Mate,
    NewAssembly,
    NewPart,
    Revolve,
    RevolveCut,
    SaveAssembly,
    SaveDrawing,
    SavePart,
    SectionView,
    SmartDimensions,
    StandardViews,
)
from swpilot.model.apply import ApplyResult, apply_to_session
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import AXIS_FEATURE_NAMES, ModelError


@dataclass
class CommandResult:
    index: int
    op: str
    source_index: int
    source_op: str
    expansion_step: int | None
    status: str  # "ok" | "error" | "skipped"
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    call_count: int = 0
    resolved: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "index": self.index,
            "op": self.op,
            "status": self.status,
            "source_index": self.source_index,
        }
        if self.source_op != self.op:
            d["expanded_from"] = self.source_op
            d["expansion_step"] = self.expansion_step
        if self.error is not None:
            d["error"] = self.error
        if self.warnings:
            d["warnings"] = self.warnings
        if self.resolved is not None:
            d["resolved"] = self.resolved
        d["call_count"] = self.call_count
        return d


@dataclass
class RunReport:
    backend: str
    schema_version: str
    success: bool
    results: list[CommandResult]
    call_log: list[dict[str, object]]
    final_state: dict[str, object]
    backend_state: dict[str, object]
    finalize_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "swpilot_version": swpilot.__version__,
            "schema_version": self.schema_version,
            "backend": self.backend,
            "success": self.success,
            "commands_total": len(self.results),
            "commands_ok": sum(1 for r in self.results if r.status == "ok"),
            "commands": [r.to_dict() for r in self.results],
            "call_log": self.call_log,
            "final_state": self.final_state,
            "backend_state": self.backend_state,
        }
        if self.finalize_error is not None:
            d["finalize_error"] = self.finalize_error
        return d


def _dir_axis(direction: str) -> tuple[str, bool]:
    """A world direction like "-y" -> (axis feature name, flip)."""
    flip = direction.startswith("-")
    return AXIS_FEATURE_NAMES[direction.lstrip("-")], flip  # type: ignore[index]


def _dispatch(backend: Backend, ec: ExpandedCommand, res: ApplyResult) -> None:
    c = ec.command
    if isinstance(c, NewPart):
        assert res.document is not None
        backend.new_part(res.document)
        backend.mark_active(res.document)
        return
    if isinstance(c, NewAssembly):
        assert res.document is not None
        backend.new_assembly(res.document)
        backend.mark_active(res.document)
        return
    if isinstance(c, CreateDrawing):
        assert res.document is not None and res.drawing is not None
        # The backend activates the model doc for the title-block
        # properties itself, then leaves the new drawing active.
        backend.create_drawing(res.drawing)
        backend.mark_active(res.document)
        return
    # Everything else operates on the twin's active document — switch the
    # backend there first (no-op when already active).
    assert res.document is not None and res.doc_kind is not None
    backend.ensure_active(res.document, res.doc_kind)
    if isinstance(c, ActivateDocument):
        return  # ensure_active above did the work (or nothing, if current)
    if isinstance(c, InsertComponent):
        assert res.component is not None
        comp = res.component
        assert comp.path is not None
        backend.insert_component(
            comp.path,
            comp.name,
            comp.translation,
            comp.rotation_row_major,
            comp.fixed,
            comp.external,
        )
        return
    if isinstance(c, Mate):
        assert res.mate is not None
        m = res.mate
        backend.add_mate(m.mate_type, m.pick_a, m.pick_b, m.value, m.name)
        return
    if isinstance(c, SaveAssembly):
        backend.save_assembly(c.path)
        return
    if isinstance(c, StandardViews | IsometricView):
        backend.add_views(res.views)
        return
    if isinstance(c, SectionView):
        assert res.section is not None
        backend.add_section_view(res.section)
        return
    if isinstance(c, SmartDimensions):
        backend.add_annotations(res.dimensions, res.notes)
        return
    if isinstance(c, SaveDrawing):
        backend.save_drawing(c.path)
        return
    if isinstance(c, CreatePlane):
        assert res.plane_display is not None
        backend.create_plane(c.name, res.plane_display, c.distance)
    elif isinstance(c, CreateAxis):
        assert res.axis_feature is not None
        backend.create_axis(c.axis, res.axis_feature)
    elif isinstance(c, CreateSketch):
        assert res.plane_display is not None
        backend.create_sketch(res.plane_display)
    elif isinstance(c, DrawRectangle):
        backend.draw_rectangle(c.center, c.width, c.height)
    elif isinstance(c, DrawCircle):
        backend.draw_circle(c.center, c.diameter)
    elif isinstance(c, DrawSlot):
        backend.draw_slot(c.start, c.end, c.width)
    elif isinstance(c, Extrude):
        assert res.feature_name is not None
        backend.extrude(c.depth, c.reverse, res.feature_name)
    elif isinstance(c, CutExtrude):
        assert res.feature_name is not None
        backend.cut_extrude(c.through_all, c.depth, c.reverse, c.draft_angle, res.feature_name)
    elif isinstance(c, Fillet):
        assert res.feature_name is not None
        backend.fillet([e.midpoint for e in res.edges], c.radius, res.feature_name)
    elif isinstance(c, Chamfer):
        assert res.feature_name is not None
        backend.chamfer([e.midpoint for e in res.edges], c.distance, c.angle, res.feature_name)
    elif isinstance(c, LinearPattern):
        assert res.feature_name is not None
        axis1, flip1 = _dir_axis(c.direction)
        dir2 = None
        if c.direction2 is not None:
            axis2, flip2 = _dir_axis(c.direction2.direction)
            dir2 = (axis2, flip2, c.direction2.spacing, c.direction2.count)
        backend.linear_pattern(
            c.features, axis1, flip1, c.spacing, c.count, dir2, res.feature_name
        )
    elif isinstance(c, CircularPattern):
        assert res.feature_name is not None
        backend.circular_pattern(
            c.features,
            AXIS_FEATURE_NAMES[c.axis],
            c.count,
            c.total_angle,
            c.equal_spacing,
            res.feature_name,
        )
    elif isinstance(c, DrawSpline):
        backend.draw_spline(list(c.points))
    elif isinstance(c, DrawArc):
        backend.draw_arc(c.center, c.start, c.end, c.ccw)
    elif isinstance(c, DrawLine):
        backend.draw_line(c.start, c.end)
    elif isinstance(c, Revolve):
        assert res.feature_name is not None and res.axis_feature is not None
        backend.revolve(res.axis_feature, c.angle, c.reverse, res.feature_name)
    elif isinstance(c, RevolveCut):
        assert res.feature_name is not None and res.axis_feature is not None
        backend.revolve_cut(res.axis_feature, c.angle, c.reverse, res.feature_name)
    elif isinstance(c, HelixThread):
        assert res.feature_name is not None
        rev = c.length / c.pitch
        backend.helix_thread(c.diameter, c.pitch, c.length, c.right_handed, rev, res.feature_name)
    elif isinstance(c, GearMeta):
        pass  # twin bookkeeping only; no COM calls
    elif isinstance(c, SavePart):
        backend.save_part(c.path)
    else:  # pragma: no cover - schema and executor must stay in sync
        raise BackendError(f"executor has no dispatch for op {c.op!r}")


def execute(
    expanded: list[ExpandedCommand],
    backend: Backend,
    schema_version: str = "0.5",
) -> RunReport:
    session = SessionTracker()
    results: list[CommandResult] = []
    failed = False
    for i, ec in enumerate(expanded):
        result = CommandResult(
            index=i,
            op=ec.command.op,
            source_index=ec.source_index,
            source_op=ec.source_op,
            expansion_step=ec.expansion_step,
            status="ok",
        )
        if failed:
            result.status = "skipped"
            results.append(result)
            continue
        calls_before = len(backend.call_log)
        try:
            res = apply_to_session(session, ec.command)
            result.warnings.extend(res.warnings)
            result.resolved = res.resolved_dict()
            _dispatch(backend, ec, res)
        except (ModelError, BackendError) as exc:
            result.status = "error"
            result.error = str(exc)
            failed = True
        except Exception as exc:
            # A COM disconnect (pywintypes.com_error), an AttributeError from
            # a dead dispatch object, or any backend bug must still produce a
            # report attributing the failure — never a bare traceback.
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            failed = True
        # Twin warnings emitted before a mid-command error would otherwise
        # strand in the session and leak onto the next command; on the
        # success path apply_to_session already drained them.
        result.warnings.extend(session.pop_warnings() + backend.pop_warnings())
        result.call_count = len(backend.call_log) - calls_before
        results.append(result)

    finalize_error: str | None = None
    if not failed:
        session.finalize()
        try:
            backend.finalize()
        except (ModelError, BackendError) as exc:
            failed = True
            finalize_error = str(exc)
        except Exception as exc:
            failed = True
            finalize_error = f"{type(exc).__name__}: {exc}"
        final_warnings = session.pop_warnings() + backend.pop_warnings()
        if final_warnings and results:
            results[-1].warnings.extend(final_warnings)

    return RunReport(
        backend=backend.name,
        schema_version=schema_version,
        success=not failed,
        results=results,
        call_log=[spec.to_dict() for spec in backend.call_log],
        final_state=session.summary(),
        backend_state=backend.state_summary(),
        finalize_error=finalize_error,
    )
