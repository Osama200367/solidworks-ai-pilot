"""Macro expansion: high-level commands become primitive sequences.

Expansion is pure and happens before any backend is involved, so macro
logic is fully testable without SolidWorks. Expansion is stateful across
the command list only through :class:`MacroContext` (e.g. a later
``add_corner_holes`` needs the plate envelope recorded by an earlier
``create_plate``).
"""

from __future__ import annotations

from dataclasses import dataclass

from swpilot.commands.schema import (
    AddCornerHoles,
    CreatePlate,
    CreateSketch,
    CutExtrude,
    DrawCircle,
    DrawRectangle,
    Extrude,
    NewPart,
    PlaneName,
    PrimitiveCommand,
)
from swpilot.tolerances import EPS


class MacroExpansionError(ValueError):
    """A macro is invalid in context (bad geometry, missing plate, ...)."""


@dataclass
class PlateInfo:
    width: float
    height: float
    thickness: float
    plane: PlaneName


@dataclass
class MacroContext:
    """State carried across macro expansion of one command list."""

    plate: PlateInfo | None = None


def expand_create_plate(cmd: CreatePlate, ctx: MacroContext) -> list[PrimitiveCommand]:
    ctx.plate = PlateInfo(cmd.width, cmd.height, cmd.thickness, cmd.plane)
    return [
        NewPart(),
        CreateSketch(plane=cmd.plane),
        DrawRectangle(center=(0.0, 0.0), width=cmd.width, height=cmd.height),
        Extrude(depth=cmd.thickness),
    ]


def expand_add_corner_holes(cmd: AddCornerHoles, ctx: MacroContext) -> list[PrimitiveCommand]:
    plate = ctx.plate
    if plate is None:
        raise MacroExpansionError(
            "add_corner_holes: no preceding create_plate in this command file; "
            "the macro needs a plate envelope to place holes in. Use create_plate "
            "first, or place holes explicitly with draw_circle + cut_extrude."
        )
    # Thresholds include the shared EPS so a macro-accepted file can never
    # fail the simulator's strict-containment/disjointness checks later.
    r = cmd.diameter / 2.0
    if cmd.margin <= r + EPS:
        raise MacroExpansionError(
            f"add_corner_holes: margin ({cmd.margin} mm) must exceed the hole radius "
            f"({r} mm), otherwise the hole crosses or touches the plate edge "
            "(SolidWorks rejects zero-thickness geometry)."
        )
    if cmd.margin >= plate.width / 2.0 or cmd.margin >= plate.height / 2.0:
        raise MacroExpansionError(
            f"add_corner_holes: margin ({cmd.margin} mm) is too large for a "
            f"{plate.width}x{plate.height} mm plate; it must be less than half of "
            "each plate dimension."
        )
    # Adjacent hole centers are (width - 2*margin) or (height - 2*margin) apart;
    # circles must stay strictly disjoint (tangent circles are zero-thickness).
    for span, dim_name in ((plate.width, "width"), (plate.height, "height")):
        gap = span - 2.0 * cmd.margin
        if gap <= cmd.diameter + EPS:
            raise MacroExpansionError(
                f"add_corner_holes: holes would overlap or touch along the plate "
                f"{dim_name} ({span} mm): centers are {gap} mm apart but the hole "
                f"diameter is {cmd.diameter} mm."
            )

    x = plate.width / 2.0 - cmd.margin
    y = plate.height / 2.0 - cmd.margin
    corners = [(-x, -y), (x, -y), (-x, y), (x, y)]
    circles: list[PrimitiveCommand] = [
        DrawCircle(center=c, diameter=cmd.diameter) for c in corners
    ]
    return [
        CreateSketch(plane=plate.plane),
        *circles,
        CutExtrude(through_all=True),
    ]
