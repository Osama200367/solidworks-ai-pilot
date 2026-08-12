"""Executor: walks expanded commands, dispatches to a backend, reports.

Fail-fast: the first command that errors stops execution; remaining
commands are reported as skipped. The report attributes every logged COM
call to the command that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import swpilot
from swpilot.backends.base import Backend, BackendError
from swpilot.commands.loader import ExpandedCommand
from swpilot.commands.schema import (
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    Extrude,
    NewPart,
    SavePart,
)


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
        }
        if self.finalize_error is not None:
            d["finalize_error"] = self.finalize_error
        return d


def _dispatch(backend: Backend, cmd: ExpandedCommand) -> None:
    c = cmd.command
    if isinstance(c, NewPart):
        backend.new_part()
    elif isinstance(c, CreateSketch):
        backend.create_sketch(c.plane)
    elif isinstance(c, DrawRectangle):
        backend.draw_rectangle(c.center, c.width, c.height)
    elif isinstance(c, DrawCircle):
        backend.draw_circle(c.center, c.diameter)
    elif isinstance(c, Extrude):
        backend.extrude(c.depth)
    elif isinstance(c, CutExtrude):
        backend.cut_extrude(c.through_all, c.depth)
    elif isinstance(c, SavePart):
        backend.save_part(c.path)
    else:  # pragma: no cover - schema and executor must stay in sync
        raise BackendError(f"executor has no dispatch for op {c.op!r}")


def execute(
    expanded: list[ExpandedCommand],
    backend: Backend,
    schema_version: str = "0.1",
) -> RunReport:
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
            _dispatch(backend, ec)
        except BackendError as exc:
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
        result.warnings = backend.pop_warnings()
        result.call_count = len(backend.call_log) - calls_before
        results.append(result)

    finalize_error: str | None = None
    if not failed:
        try:
            backend.finalize()
        except BackendError as exc:
            failed = True
            finalize_error = str(exc)
        except Exception as exc:
            failed = True
            finalize_error = f"{type(exc).__name__}: {exc}"
        final_warnings = backend.pop_warnings()
        if final_warnings and results:
            results[-1].warnings.extend(final_warnings)

    return RunReport(
        backend=backend.name,
        schema_version=schema_version,
        success=not failed,
        results=results,
        call_log=[spec.to_dict() for spec in backend.call_log],
        final_state=backend.state_summary(),
        finalize_error=finalize_error,
    )
