"""Load, validate and macro-expand SW-Pilot command files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from swpilot.commands import macros
from swpilot.commands.schema import (
    AddCornerHoles,
    Command,
    CommandFile,
    CreatePlate,
    PrimitiveCommand,
)


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


def expand_commands(commands: list[Command]) -> list[ExpandedCommand]:
    """Expand macros into primitives, preserving provenance.

    Raises :class:`CommandFileError` when a macro is invalid in context
    (e.g. ``add_corner_holes`` without a plate, or holes that cannot fit).
    """
    ctx = macros.MacroContext()
    out: list[ExpandedCommand] = []
    for i, cmd in enumerate(commands):
        try:
            if isinstance(cmd, CreatePlate):
                expansion = macros.expand_create_plate(cmd, ctx)
            elif isinstance(cmd, AddCornerHoles):
                expansion = macros.expand_add_corner_holes(cmd, ctx)
            else:
                out.append(ExpandedCommand(command=cmd, source_index=i, source_op=cmd.op))
                continue
        except macros.MacroExpansionError as exc:
            raise CommandFileError(f"commands[{i}] ({cmd.op}): {exc}") from exc
        out.extend(
            ExpandedCommand(command=prim, source_index=i, source_op=cmd.op, expansion_step=step)
            for step, prim in enumerate(expansion)
        )
    return out


def load_and_expand(path: str | Path) -> tuple[CommandFile, list[ExpandedCommand]]:
    """Convenience: load, validate, and expand in one call."""
    cmd_file = load_command_file(path)
    return cmd_file, expand_commands(list(cmd_file.commands))
