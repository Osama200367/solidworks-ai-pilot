"""Auto-generated command vocabulary for the LLM prompt bundle.

Walks the pydantic ``Command`` discriminated union and renders a compact
op catalog — every op with its fields, types, enum values and defaults —
so the reference can never drift from the real schema. A CI test asserts
every ``PRIMITIVE_OPS ∪ MACRO_OPS`` appears. Curated guidance the models
can't express (units, macro preference, English keys) is appended.
"""

from __future__ import annotations

import types
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from swpilot.commands import schema as sc

# Ops the LLM should almost never hand-write (a macro or the drawing flow
# already produces them) — kept in the catalog but flagged.
_LOW_LEVEL_HINT = {
    "draw_spline", "draw_arc", "draw_line", "gear_meta",
}


def _command_models() -> list[type[BaseModel]]:
    union = get_args(sc.Command)[0]  # Annotated[Union[...], FieldInfo]
    return list(get_args(union))


def _render_type(ann: Any) -> str:
    """A short, LLM-friendly rendering of a field annotation."""
    origin = get_origin(ann)
    # unwrap Annotated[...]
    if origin is Annotated:
        return _render_type(get_args(ann)[0])
    if ann is type(None):
        return "null"
    if ann in (float,):
        return "number"
    if ann is int:
        return "integer"
    if ann is str:
        return "string"
    if ann is bool:
        return "boolean"
    if origin is Literal:
        vals = get_args(ann)
        if len(vals) == 1:
            return f'"{vals[0]}"'
        return "one of [" + ", ".join(f'"{v}"' for v in vals) + "]"
    if origin in (Union, types.UnionType):
        parts = [a for a in get_args(ann) if a is not type(None)]
        rendered = " | ".join(_render_type(p) for p in parts)
        if type(None) in get_args(ann):
            rendered += " | null"
        return rendered
    if origin in (list,):
        return f"[{_render_type(get_args(ann)[0])}, ...]"
    if origin in (tuple,):
        args = get_args(ann)
        # Only float tuples are coordinate points ("[x, y]" = a mm point per
        # the legend). Integer tuples like ScaleRatio (tuple[PositiveInt, ...])
        # must render distinctly so a 1:2 scale isn't labelled a mm point.
        if len(args) == 2 and all(_is_float(a) for a in args):
            return "[x, y]"
        if len(args) == 3 and all(_is_float(a) for a in args):
            return "[x, y, z]"
        return "[" + ", ".join(_render_type(a) for a in args) + "]"
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return _render_subobject(ann)
    return getattr(ann, "__name__", str(ann))


def _is_float(ann: Any) -> bool:
    """True for a float (possibly Annotated) — i.e. a coordinate component."""
    if get_origin(ann) is Annotated:
        return _is_float(get_args(ann)[0])
    return ann is float


def _render_subobject(model: type[BaseModel]) -> str:
    """Inline shape of a nested sub-object (e.g. keyway, a mate entity)."""
    fields = []
    for name, f in model.model_fields.items():
        fields.append(name if f.is_required() else f"{name}?")
    return "{" + ", ".join(fields) + "}"


def _field_line(name: str, f: FieldInfo) -> str:
    t = _render_type(f.annotation)
    if f.is_required():
        return f"    {name}: {t}"
    # Resolve the real default, running any default_factory (otherwise a
    # factory field surfaces the PydanticUndefined sentinel into the prompt).
    default = f.get_default(call_default_factory=True)
    if default is None:
        return f"    {name}?: {t}"
    if isinstance(default, str):
        return f'    {name}?: {t} (default "{default}")'
    return f"    {name}?: {t} (default {default})"


def _op_block(model: type[BaseModel]) -> str:
    op = model.model_fields["op"].default
    doc = (model.__doc__ or "").strip().split("\n")[0]
    lines = [f'  op "{op}" — {doc}']
    if op in _LOW_LEVEL_HINT:
        lines[0] += "  [low-level; prefer a macro]"
    for name, f in model.model_fields.items():
        if name == "op":
            continue
        lines.append(_field_line(name, f))
    return "\n".join(lines)


_GUIDANCE = f"""\
GUIDANCE (read carefully):
- Output ONE JSON object: {{"schema_version": "{sc.SCHEMA_VERSION}", "commands": [ ... ]}}.
- All lengths are in MILLIMETERS; angles in degrees. Never include units in values.
- JSON keys and enum values are ALWAYS English, whatever language the request is in.
- PREFER high-level macros over hand-writing primitive sequences:
  * a rectangular plate → "create_plate" (not new_part + sketch + extrude)
  * holes → "hole" (supports counterbore/countersink and metric "standard" like "M6")
  * a bolt pattern in an assembly → "bolt_circle"
  * a gear → "involute_spur_gear"; a sprocket → "sprocket_iso"
- A part is one document; an assembly inserts components and mates them. Build each
  part (and "save_part" it) before inserting it as a component.
- Only include fields you need; optional fields fall back to the defaults shown.
- Emit ONLY the JSON object — no prose, no markdown fences, no comments."""


def vocabulary_text() -> str:
    """The full command vocabulary reference for the prompt bundle."""
    models = _command_models()
    blocks = [_op_block(m) for m in models]
    header = (
        "COMMAND VOCABULARY (schema "
        f"{sc.SCHEMA_VERSION}). Each command is a JSON object with an \"op\" key.\n"
        'Notation: name = required, name? = optional, "x" = a literal string,\n'
        "one of [...] = allowed values, [x, y] = a 2D point (mm), {...} = a sub-object."
    )
    return header + "\n\n" + "\n\n".join(blocks) + "\n\n" + _GUIDANCE


def all_ops() -> set[str]:
    """Every op the vocabulary documents (for the coverage test)."""
    return {m.model_fields["op"].default for m in _command_models()}
