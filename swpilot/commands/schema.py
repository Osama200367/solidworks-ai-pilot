"""Pydantic models for the SW-Pilot command schema (v0.1).

Two tiers share one discriminated union keyed on ``op``:

* primitives — map 1:1 onto backend operations (and thence COM calls)
* macros — expand into primitives before execution (see ``macros.py``)

All lengths are millimeters. NaN/inf are rejected everywhere.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1"


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

PlaneName = Literal["front", "top", "right"]


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


class NewPart(_Cmd):
    """Create a new, empty part document."""

    op: Literal["new_part"] = "new_part"


class CreateSketch(_Cmd):
    """Open a sketch on one of the standard reference planes."""

    op: Literal["create_sketch"] = "create_sketch"
    plane: PlaneName = "front"


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


class Extrude(_Cmd):
    """Blind boss-extrude the active sketch by ``depth`` mm."""

    op: Literal["extrude"] = "extrude"
    depth: PositiveMm


class CutExtrude(_Cmd):
    """Cut-extrude the active sketch: through-all (default) or blind.

    Giving ``depth`` implies a blind cut, so ``through_all`` may be
    omitted in that case; setting both ``through_all=true`` and ``depth``
    is a contradiction and is rejected.
    """

    op: Literal["cut_extrude"] = "cut_extrude"
    through_all: bool = True
    depth: PositiveMm | None = None

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
        return self


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
    plane: PlaneName = "front"


class AddCornerHoles(_Cmd):
    """Four through-holes, one per plate corner.

    ``margin`` is the distance from each pair of adjacent edges to the
    hole center. Requires a preceding ``create_plate`` to define the
    plate envelope.
    """

    op: Literal["add_corner_holes"] = "add_corner_holes"
    diameter: PositiveMm
    margin: PositiveMm


PrimitiveCommand = Annotated[
    NewPart | CreateSketch | DrawRectangle | DrawCircle | Extrude | CutExtrude | SavePart,
    Field(discriminator="op"),
]

MacroCommand = Annotated[CreatePlate | AddCornerHoles, Field(discriminator="op")]

Command = Annotated[
    NewPart
    | CreateSketch
    | DrawRectangle
    | DrawCircle
    | Extrude
    | CutExtrude
    | SavePart
    | CreatePlate
    | AddCornerHoles,
    Field(discriminator="op"),
]

PRIMITIVE_OPS = frozenset(
    {
        "new_part",
        "create_sketch",
        "draw_rectangle",
        "draw_circle",
        "extrude",
        "cut_extrude",
        "save_part",
    }
)
MACRO_OPS = frozenset({"create_plate", "add_corner_holes"})


class CommandFile(BaseModel):
    """Top-level structure of a SW-Pilot command file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1"]
    commands: list[Command] = Field(min_length=1)
