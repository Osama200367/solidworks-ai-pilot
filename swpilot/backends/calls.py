"""Shared COM call plan: the single source of truth for SolidWorks calls.

Each builder returns :class:`CallSpec` objects describing exactly one COM
invocation. The mock backend *logs* these specs; the COM backend logs the
same specs and *executes* them. Keeping construction here means the call
log CI asserts on cannot drift from what runs on Windows.

Pure Python, importable everywhere. Units: builders take millimeters and
emit meters, because the SolidWorks API works in meters.

Constant values (``swconst`` enums) are hardcoded rather than read from
the type library so this module needs no COM; they are stable across
SolidWorks 2022-2025.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MM_TO_M = 1e-3

# swconst.swEndConditions_e
SW_END_COND_BLIND = 0
SW_END_COND_THROUGH_ALL = 1
# swconst.swUserPreferenceStringValue_e
SW_DEFAULT_TEMPLATE_PART = 8
# swconst.swSaveAsVersion_e / swconst.swSaveAsOptions_e
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_AS_OPTIONS_SILENT = 1

PLANE_NAMES: dict[str, str] = {
    "front": "Front Plane",
    "top": "Top Plane",
    "right": "Right Plane",
}


@dataclass(frozen=True)
class CallSpec:
    """One COM invocation: ``<target>.<method>(*args)`` or a property set.

    ``target`` is a dotted path resolved by the COM backend relative to
    the live application ("App") or active model document ("Model").
    ``check`` tells the COM backend how to interpret the return value:

    * ``none`` — ignore the result
    * ``truthy`` — raise BackendError unless the result is truthy
    * ``non_null`` — raise BackendError if the result is None
    """

    target: str
    method: str
    args: tuple[object, ...] = ()
    kind: Literal["call", "set"] = "call"
    value: object = None
    check: Literal["none", "truthy", "non_null"] = "none"
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "target": self.target,
            "method": self.method,
        }
        if self.kind == "set":
            d["kind"] = "set"
            d["value"] = self.value
        else:
            d["args"] = list(self.args)
        if self.note:
            d["note"] = self.note
        return d


# --------------------------------------------------------------------------
# Builders (one group per primitive command)
# --------------------------------------------------------------------------


def get_default_part_template() -> CallSpec:
    return CallSpec(
        target="App",
        method="GetUserPreferenceStringValue",
        args=(SW_DEFAULT_TEMPLATE_PART,),
        check="truthy",
        note="resolve default part template path (swDefaultTemplatePart)",
    )


def new_document(template: str) -> CallSpec:
    return CallSpec(
        target="App",
        method="NewDocument",
        args=(template, 0, 0.0, 0.0),
        check="non_null",
        note="create new part document from template",
    )


def new_part_calls(template: str = "<default part template>") -> list[CallSpec]:
    """The call plan for ``new_part``.

    The mock logs a placeholder template path; the COM backend resolves
    the real one (preference or override) and logs that instead.
    """
    return [get_default_part_template(), new_document(template)]


def create_sketch_calls(plane: str) -> list[CallSpec]:
    plane_name = PLANE_NAMES[plane]
    return [
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(plane_name, "PLANE", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select reference plane '{plane_name}' (English-language UI names)",
        ),
        CallSpec(
            target="Model.SketchManager",
            method="InsertSketch",
            args=(True,),
            note="open a sketch on the selected plane",
        ),
        CallSpec(
            target="Model.SketchManager",
            method="AddToDB",
            kind="set",
            value=True,
            note="add entities directly to DB (disables grid/entity snapping)",
        ),
    ]


def draw_rectangle_calls(
    center: tuple[float, float], width: float, height: float
) -> list[CallSpec]:
    cx, cy = center
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateCenterRectangle",
            args=(
                cx * MM_TO_M,
                cy * MM_TO_M,
                0.0,
                (cx + width / 2.0) * MM_TO_M,
                (cy + height / 2.0) * MM_TO_M,
                0.0,
            ),
            check="non_null",
            note=f"center rectangle {width}x{height} mm at ({cx}, {cy}) mm",
        ),
    ]


def draw_circle_calls(center: tuple[float, float], diameter: float) -> list[CallSpec]:
    cx, cy = center
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateCircleByRadius",
            args=(cx * MM_TO_M, cy * MM_TO_M, 0.0, (diameter / 2.0) * MM_TO_M),
            check="non_null",
            note=f"circle d={diameter} mm at ({cx}, {cy}) mm",
        ),
    ]


def _end_sketch_edits() -> list[CallSpec]:
    return [
        CallSpec(
            target="Model.SketchManager",
            method="AddToDB",
            kind="set",
            value=False,
            note="restore normal sketch entity insertion",
        ),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection so the feature operates on the active sketch",
        ),
    ]


def extrude_calls(depth_mm: float) -> list[CallSpec]:
    # IFeatureManager.FeatureExtrusion2(
    #   Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
    #   Dang1, Dang2, OffsetReverse1, OffsetReverse2,
    #   TranslateSurface1, TranslateSurface2, Merge, UseFeatScope, UseAutoSelect)
    return [
        *_end_sketch_edits(),
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureExtrusion2",
            args=(
                True,  # Sd: single-ended
                False,  # Flip: do not flip side to cut (n/a for boss)
                False,  # Dir: do not flip extrude direction
                SW_END_COND_BLIND,  # T1
                SW_END_COND_BLIND,  # T2 (unused, single-ended)
                depth_mm * MM_TO_M,  # D1
                0.0,  # D2
                False, False,  # Dchk1, Dchk2: no draft
                False, False,  # Ddir1, Ddir2
                0.0, 0.0,  # Dang1, Dang2
                False, False,  # OffsetReverse1, OffsetReverse2
                False, False,  # TranslateSurface1, TranslateSurface2
                True,  # Merge result
                False,  # UseFeatScope
                True,  # UseAutoSelect: operate on the active sketch
            ),
            check="non_null",
            note=f"blind boss-extrude {depth_mm} mm (depth in meters)",
        ),
    ]


def cut_extrude_calls(through_all: bool, depth_mm: float | None) -> list[CallSpec]:
    # IFeatureManager.FeatureCut3(
    #   Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
    #   Dang1, Dang2, OffsetReverse1, OffsetReverse2,
    #   TranslateSurface1, TranslateSurface2, NormalCut, UseFeatScope,
    #   UseAutoSelect, AssemblyFeatureScope, AutoSelectComponents,
    #   PropagateFeatureToParts)
    if through_all:
        t1 = SW_END_COND_THROUGH_ALL
        d1 = 0.0
        note = "cut-extrude through all"
    else:
        assert depth_mm is not None  # schema guarantees this
        t1 = SW_END_COND_BLIND
        d1 = depth_mm * MM_TO_M
        note = f"blind cut-extrude {depth_mm} mm (depth in meters)"
    return [
        *_end_sketch_edits(),
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureCut3",
            args=(
                True,  # Sd: single-ended
                False,  # Flip: remove material inside the profile
                False,  # Dir: same direction as the base extrude
                t1,  # T1
                SW_END_COND_BLIND,  # T2 (unused)
                d1,  # D1
                0.0,  # D2
                False, False,  # Dchk1, Dchk2
                False, False,  # Ddir1, Ddir2
                0.0, 0.0,  # Dang1, Dang2
                False, False,  # OffsetReverse1, OffsetReverse2
                False, False,  # TranslateSurface1, TranslateSurface2
                False,  # NormalCut
                False,  # UseFeatScope
                True,  # UseAutoSelect
                False,  # AssemblyFeatureScope
                True,  # AutoSelectComponents
                False,  # PropagateFeatureToParts
            ),
            check="non_null",
            note=note,
        ),
    ]


def save_part_calls(path: str) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model",
            method="SaveAs3",
            args=(path, SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT),
            check="truthy",
            note="save part (silent)",
        ),
    ]


def finalize_calls() -> list[CallSpec]:
    return [
        CallSpec(
            target="Model",
            method="ViewZoomtofit2",
            note="zoom to fit for visibility",
        ),
    ]
