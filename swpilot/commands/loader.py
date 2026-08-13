"""Load, validate and macro-expand SW-Pilot command files.

Expansion runs a :class:`ModelTracker` pass over every emitted
primitive, so both macro errors *and* geometric errors (bad selectors,
cuts outside material, unknown planes) surface at validation time —
``swpilot validate`` needs no backend to catch them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from swpilot.commands import macros
from swpilot.commands.schema import (
    AddCornerHoles,
    BoltCircle,
    CircularPattern,
    Command,
    CommandFile,
    CreatePlate,
    CreateSketch,
    Hole,
    LinearPattern,
    PrimitiveCommand,
)
from swpilot.model.apply import apply_to_session
from swpilot.model.session import SessionTracker
from swpilot.model.tracker import ModelError


class CommandFileError(ValueError):
    """The command file is unreadable, malformed, or semantically invalid."""


@dataclass(frozen=True)
class ExpandedCommand:
    """A primitive ready for execution, with provenance for reporting.

    ``source_index`` is the index into the original ``commands`` array;
    ``source_op`` is the op the user wrote there. For raw primitives
    ``expansion_step`` is None; for macro output it is the 0-based step
    within that macro's expansion.
    """

    command: PrimitiveCommand
    source_index: int
    source_op: str
    expansion_step: int | None = None


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["invalid command file:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  {loc or '<root>'}: {err['msg']}")
    return "\n".join(lines)


def parse_command_data(data: object) -> CommandFile:
    """Validate already-parsed JSON data into a :class:`CommandFile`."""
    try:
        return CommandFile.model_validate(data)
    except ValidationError as exc:
        raise CommandFileError(_format_validation_error(exc)) from exc


def load_command_file(path: str | Path) -> CommandFile:
    """Read and validate a JSON command file."""
    p = Path(path)
    try:
        # utf-8-sig transparently strips a UTF-8 BOM (common from Windows
        # editors) and is byte-identical for BOM-less files.
        raw = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CommandFileError(
            f"cannot read {p}: file is not UTF-8 encoded ({exc}); re-save it as UTF-8"
        ) from exc
    except OSError as exc:
        raise CommandFileError(f"cannot read {p}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandFileError(f"{p} is not valid JSON: {exc}") from exc
    return parse_command_data(data)


def _expand_one(cmd: Command, session: SessionTracker) -> list[object] | None:
    """Expansion for one source command; None means pass through as-is."""
    if isinstance(cmd, CreatePlate):
        return macros.expand_create_plate(cmd)
    if isinstance(cmd, AddCornerHoles):
        return macros.expand_add_corner_holes(cmd, session.active_part("add_corner_holes"))
    if isinstance(cmd, Hole):
        return macros.expand_hole(cmd, session.active_part("hole"))
    if isinstance(cmd, CreateSketch) and cmd.on is not None:
        return macros.expand_sketch_on_face(cmd, session.active_part("create_sketch"))
    if isinstance(cmd, LinearPattern | CircularPattern):
        return macros.expand_pattern_axes(cmd, session.active_part(cmd.op))
    if isinstance(cmd, BoltCircle):
        return macros.expand_bolt_circle(cmd, session)
    return None


def expand_commands(commands: list[Command]) -> list[ExpandedCommand]:
    """Expand macros into primitives, preserving provenance.

    Every emitted primitive is validated against a session twin as
    expansion proceeds, so macro errors and geometric errors alike raise
    :class:`CommandFileError` naming the offending source command.
    """
    session = SessionTracker()
    out: list[ExpandedCommand] = []
    for i, cmd in enumerate(commands):
        try:
            expansion = _expand_one(cmd, session)
        except (macros.MacroExpansionError, ModelError) as exc:
            raise CommandFileError(f"commands[{i}] ({cmd.op}): {exc}") from exc
        primitives: list[object] = [cmd] if expansion is None else expansion
        was_macro = expansion is not None
        for step, prim in enumerate(primitives):
            try:
                apply_to_session(session, prim)  # type: ignore[arg-type]
            except ModelError as exc:
                raise CommandFileError(f"commands[{i}] ({cmd.op}): {exc}") from exc
            out.append(
                ExpandedCommand(
                    command=prim,  # type: ignore[arg-type]
                    source_index=i,
                    source_op=cmd.op,
                    expansion_step=step if was_macro else None,
                )
            )
    return out


def load_and_expand(path: str | Path) -> tuple[CommandFile, list[ExpandedCommand]]:
    """Convenience: load, validate, and expand in one call."""
    cmd_file = load_command_file(path)
    return cmd_file, expand_commands(list(cmd_file.commands))
