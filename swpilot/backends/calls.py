# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Shared COM call plan: the single source of truth for SolidWorks calls.

Each builder returns :class:`CallSpec` objects describing exactly one COM
invocation. The mock backend *logs* these specs; the COM backend logs the
same specs and *executes* them. Keeping construction here means the call
log CI asserts on cannot drift from what runs on Windows.

Pure Python, importable everywhere. Units: builders take millimeters and
degrees and emit meters and radians, because the SolidWorks API works in
meters/radians.

Constant values (``swconst`` enums) are hardcoded rather than read from
the type library so this module needs no COM; they are stable across
SolidWorks 2022-2025.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

MM_TO_M = 1e-3

# swconst.swEndConditions_e
SW_END_COND_BLIND = 0
SW_END_COND_THROUGH_ALL = 1
# swconst.swUserPreferenceStringValue_e
SW_DEFAULT_TEMPLATE_PART = 8
SW_DEFAULT_TEMPLATE_ASSEMBLY = 9
# swconst.swSaveAsVersion_e / swconst.swSaveAsOptions_e
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_AS_OPTIONS_SILENT = 1
# swconst.swRefPlaneReferenceConstraints_e
SW_REF_PLANE_DISTANCE = 8
# swRefPlaneReferenceConstraint_OptionFlip = 0x100 (256); 512 is
# OptionOriginOnCurve. Offset planes on the negative side use 8|256 = 264,
# matching macro-recorder output.
SW_REF_PLANE_OPTION_FLIP = 256
# IFeatureManager::FeatureFillet3 options: value produced by the SolidWorks
# macro recorder for a plain constant-radius edge fillet.
SW_FILLET_DEFAULT_OPTIONS = 195
# IFeatureManager::InsertFeatureChamfer: chamfer with edges (not face/vertex),
# angle-distance type — values as produced by the macro recorder.
SW_CHAMFER_OPTIONS_EDGE = 4
SW_CHAMFER_TYPE_ANGLE_DISTANCE = 1
# ISketchManager::CreateSketchSlot: straight slot, center-to-center length.
SW_SLOT_CREATION_LINE = 0
SW_SLOT_LENGTH_CENTER_CENTER = 0

# Selection marks used by pattern features.
SW_MARK_PATTERN_DIRECTION1 = 1
SW_MARK_PATTERN_DIRECTION2 = 2
SW_MARK_PATTERN_FEATURES = 4

# swconst.swMateType_e
SW_MATE_TYPES: dict[str, int] = {
    "coincident": 0,
    "concentric": 1,
    "parallel": 3,
    "distance": 5,
    "width": 11,
}
# swconst.swMateAlign_e: CLOSEST lets SolidWorks pick the non-flipping
# configuration for the components' current positions, which the twin has
# already made geometrically consistent.
SW_MATE_ALIGN_CLOSEST = 2
# swconst.swAddComponentConfigOptions_e
SW_ADD_COMPONENT_CURRENT_CONFIG = 0
# swconst.swDocumentTypes_e / swOpenDocOptions_e
SW_DOC_PART = 1
SW_OPEN_DOC_SILENT = 1
# Both-document selection entity mark for mates.
SW_MARK_MATE_ENTITY = 1

# -- drawings (v0.4) -------------------------------------------------------
# swconst.swUserPreferenceStringValue_e
SW_DEFAULT_TEMPLATE_DRAWING = 10
# swconst.swDwgPaperSizes_e — landscape metric sheets. The US sizes occupy
# 0-5 (A..E with AsizeVertical at 1), then A4 landscape=6, A4 portrait=7,
# A3=8, A2=9, A1=10, A0=11, user-defined=12.
SW_PAPER_SIZES: dict[str, int] = {"A4": 6, "A3": 8}
# swconst.swDwgTemplates_e mirrors the paper-size indexing for the standard
# sizes (used as ISheet::SetProperties' TemplateIn so the sheet keeps the
# format matching its size).
SW_SHEET_TEMPLATES: dict[str, int] = {"A4": 6, "A3": 8}
# swconst.swDimensionTextParts_e
SW_DIM_TEXT_PREFIX = 1
SW_DIM_TEXT_CALLOUT_BELOW = 4
# swconst.swCustomInfoType_e
SW_CUSTOM_INFO_TEXT = 30
# swconst.swCustomPropertyAddOption_e: delete-and-add = overwrite existing
SW_CUSTOM_PROP_DELETE_AND_ADD = 1
# The default drawing template names its sheet "Sheet1"; used to return
# focus to the sheet after view-activated sketching.
SW_SHEET1 = "Sheet1"


@dataclass(frozen=True)
class CallSpec:
    """One COM invocation: ``<target>.<method>(*args)`` or a property set.

    ``target`` is a dotted path resolved by the COM backend relative to
    the live application ("App") or active model document ("Model"); the
    special target "LastFeature" resolves to the most recently created
    feature object (for renaming). ``check`` tells the COM backend how to
    interpret the return value:

    * ``none`` — ignore the result
    * ``truthy`` — raise BackendError unless the result is truthy
    * ``non_null`` — raise BackendError if the result is None
    * ``status_zero`` — raise BackendError unless the result is 0 (or
      None): for Long-status COM methods like SaveAs3 where 0 means
      success and nonzero carries error bits (swFileSaveError_e)

    ``remember``: the COM backend keeps the (non-null) result as the
    "last feature" for a following LastFeature rename spec.
    """

    target: str
    method: str
    args: tuple[object, ...] = ()
    kind: Literal["call", "set"] = "call"
    value: object = None
    check: Literal["none", "truthy", "non_null", "status_zero"] = "none"
    remember: bool = False
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


def rename_last_feature(name: str) -> CallSpec:
    """Rename the just-created feature to the tracker's name.

    Keeps SolidWorks' feature tree names identical to the twin's, so
    later name-based references (of_feature, pattern seeds) are exact.
    """
    return CallSpec(
        target="LastFeature",
        method="Name",
        kind="set",
        value=name,
        note=f"rename feature to '{name}' (twin name parity)",
    )


# --------------------------------------------------------------------------
# Builders (one group per primitive command)
# --------------------------------------------------------------------------


def get_default_part_template() -> CallSpec:
    # check="none": an unset template legitimately returns ""; new_part in
    # the COM backend detects that and raises its tailored guidance error.
    return CallSpec(
        target="App",
        method="GetUserPreferenceStringValue",
        args=(SW_DEFAULT_TEMPLATE_PART,),
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


def new_assembly_document(template: str) -> CallSpec:
    return CallSpec(
        target="App",
        method="NewDocument",
        args=(template, 0, 0.0, 0.0),
        check="non_null",
        note="create new assembly document from template",
    )


def new_assembly_calls(template: str = "<default assembly template>") -> list[CallSpec]:
    """The call plan for ``new_assembly`` (template resolution like new_part)."""
    return [
        CallSpec(
            target="App",
            method="GetUserPreferenceStringValue",
            args=(SW_DEFAULT_TEMPLATE_ASSEMBLY,),
            note="resolve default assembly template path (swDefaultTemplateAssembly)",
        ),
        new_assembly_document(template),
    ]


def activate_document_calls(logical_name: str) -> list[CallSpec]:
    """Switch the active document.

    Documented mock/real divergence (like the template placeholder): the
    mock logs the session-logical document name; the COM backend passes
    the document's real window title (captured at creation), since
    ActivateDoc2 addresses documents by title.
    """
    return [
        CallSpec(
            target="App",
            method="ActivateDoc2",
            args=(logical_name, False, 0),
            check="non_null",
            note=f"activate document '{logical_name}' (COM uses its real title; "
            "trailing 0 is the ByRef Errors slot under late binding)",
        ),
    ]


def insert_component_calls(
    path: str, name: str, translation_mm: tuple[float, float, float]
) -> list[CallSpec]:
    """Insert a component from a file at a position, then rename it.

    IComponent2 exposes ``Name2`` (settable) rather than ``Name`` — the
    rename spec reuses the LastFeature mechanism with that property.
    """
    x, y, z = translation_mm
    return [
        # ISldWorks/IAssemblyDoc AddComponent5(CompName, ConfigOption,
        #   NewConfigName, UseConfigForPartReferences, ExistingConfigName,
        #   X, Y, Z) -> IComponent2
        CallSpec(
            target="Model",
            method="AddComponent5",
            args=(
                path,
                SW_ADD_COMPONENT_CURRENT_CONFIG,
                "",
                False,
                "",
                x * MM_TO_M,
                y * MM_TO_M,
                z * MM_TO_M,
            ),
            check="non_null",
            remember=True,
            note=f"insert component '{name}' from {path} at ({x}, {y}, {z}) mm",
        ),
        CallSpec(
            target="LastFeature",
            method="Name2",
            kind="set",
            value=name,
            note=f"rename component instance to '{name}' (twin name parity)",
        ),
    ]


def open_external_part_calls(path: str) -> list[CallSpec]:
    """Load an external part file into memory.

    AddComponent5 requires the source document to be open in the session;
    same-run parts already are, external files are not. The two trailing
    0s fill the ByRef Errors/Warnings slots (replaced with VARIANTs at
    execution time by the COM backend).
    """
    return [
        CallSpec(
            target="App",
            method="OpenDoc6",
            args=(path, SW_DOC_PART, SW_OPEN_DOC_SILENT, "", 0, 0),
            check="non_null",
            note="open external part in memory (AddComponent5 prerequisite)",
        ),
    ]


def component_transform_calls(
    name: str, rotation_row_major: list[float], translation_mm: tuple[float, float, float]
) -> list[CallSpec]:
    """Apply a rotation+translation to a component via IComponent2.Transform2.

    The 16-float math-transform layout is rows of R (9), translation in
    meters (3), scale (1), then three zeros. The COM backend builds the
    IMathTransform via IMathUtility.CreateTransform; the mock logs the
    same 16 numbers.
    """
    t = [v * MM_TO_M for v in translation_mm]
    # IMathTransform.ArrayData rows are the IMAGES of the local axes — the
    # transpose of our row-major (world = R @ local) rotation matrix.
    r = rotation_row_major
    transposed = [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]]
    data16 = tuple(transposed + t + [1.0, 0.0, 0.0, 0.0])
    return [
        CallSpec(
            target=f"Component:{name}",
            method="Transform2",
            kind="set",
            value=data16,
            note=f"orient component '{name}' (row-major R, translation in meters)",
        ),
        CallSpec(
            target="Model",
            method="EditRebuild3",
            note="rebuild after component transform",
        ),
    ]


def fix_component_calls(name: str, assembly_hint: str = "<asm>") -> list[CallSpec]:
    """Fix a component in place.

    Divergence note: component selection strings are '<instance>@<title>';
    the mock logs the '<asm>' placeholder, the COM backend substitutes the
    assembly's real title.
    """
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for fix",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(f"{name}@{assembly_hint}", "COMPONENT", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select component '{name}' to fix",
        ),
        CallSpec(
            target="Model",
            method="FixComponent",
            note="fix the selected component",
        ),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after fix",
        ),
    ]


def add_mate_calls(
    mate_type: str,
    pick_a_mm: tuple[float, float, float],
    pick_b_mm: tuple[float, float, float],
    value_mm: float | None,
    name: str,
) -> list[CallSpec]:
    """Select two component faces by world coordinates and mate them.

    Cylindrical faces are also selected with entity type "FACE" — the
    pick point lies on the cylinder wall.
    """
    out = [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for mate",
        )
    ]
    for px, py, pz in (pick_a_mm, pick_b_mm):
        out.append(
            CallSpec(
                target="Model.Extension",
                method="SelectByID2",
                args=(
                    "",
                    "FACE",
                    px * MM_TO_M,
                    py * MM_TO_M,
                    pz * MM_TO_M,
                    True,
                    SW_MARK_MATE_ENTITY,
                    None,
                    0,
                ),
                check="truthy",
                note=f"select mate face at ({px:.4g}, {py:.4g}, {pz:.4g}) mm",
            )
        )
    out.append(
        # IAssemblyDoc.AddMate5(MateTypeFromEnum, AlignFromEnum, Flip,
        #   Distance, DistanceAbsUpperLimit, DistanceAbsLowerLimit,
        #   GearRatioNumerator, GearRatioDenominator, Angle,
        #   AngleAbsUpperLimit, AngleAbsLowerLimit, ForPositioningOnly,
        #   LockRotation, WidthMateOption, ErrorStatus) -> IMate2
        # ErrorStatus is ByRef; the trailing 0 fills its slot under late
        # binding (pywin32 returns out-params alongside the result).
        CallSpec(
            target="Model",
            method="AddMate5",
            args=(
                SW_MATE_TYPES[mate_type],
                SW_MATE_ALIGN_CLOSEST,
                False,  # Flip
                # Equal limit slots equal to the distance denote a plain
                # (non-limit) distance mate, matching macro-recorder output.
                (value_mm or 0.0) * MM_TO_M,  # Distance
                (value_mm or 0.0) * MM_TO_M,  # DistanceAbsUpperLimit
                (value_mm or 0.0) * MM_TO_M,  # DistanceAbsLowerLimit
                0.0,
                0.0,  # gear ratio
                0.0,
                0.0,
                0.0,  # angle + limits
                False,  # ForPositioningOnly
                False,  # LockRotation
                0,  # WidthMateOption
                0,  # ErrorStatus (ByRef slot)
            ),
            check="non_null",
            remember=True,
            note=f"{mate_type} mate '{name}'"
            + (f" at {value_mm} mm" if value_mm is not None else ""),
        )
    )
    out.append(rename_last_feature(name))
    out.append(
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after mate",
        )
    )
    return out


def save_assembly_calls(path: str) -> list[CallSpec]:
    """Save the assembly (same SaveAs3 semantics as save_part)."""
    return [
        CallSpec(
            target="Model",
            method="SaveAs3",
            args=(path, SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT),
            check="status_zero",
            note="save assembly (silent); returns swFileSaveError_e status, 0 = success",
        ),
    ]


def create_plane_calls(name: str, base_display: str, distance_mm: float) -> list[CallSpec]:
    flip = distance_mm < 0
    options = SW_REF_PLANE_DISTANCE | (SW_REF_PLANE_OPTION_FLIP if flip else 0)
    return [
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(base_display, "PLANE", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select base plane '{base_display}' for offset reference plane",
        ),
        # remember stays False: InsertRefPlane returns an IRefPlane, which
        # has no Name property under dynamic dispatch. Leaving _last_feature
        # unset makes the rename spec fall back to FeatureByPositionReverse(0),
        # which returns the newest IFeature (Name settable) — the same
        # mechanism used after InsertAxis2.
        CallSpec(
            target="Model.FeatureManager",
            method="InsertRefPlane",
            args=(options, abs(distance_mm) * MM_TO_M, 0, 0.0, 0, 0.0),
            check="non_null",
            note=f"offset reference plane {distance_mm} mm from {base_display}",
        ),
        rename_last_feature(name),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after plane creation",
        ),
    ]


_AXIS_PLANE_PAIRS: dict[str, tuple[str, str]] = {
    # world axis -> the two standard planes whose intersection is that axis
    "x": ("Front Plane", "Top Plane"),
    "y": ("Front Plane", "Right Plane"),
    "z": ("Top Plane", "Right Plane"),
}


def create_axis_calls(axis: str, name: str) -> list[CallSpec]:
    p1, p2 = _AXIS_PLANE_PAIRS[axis]
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for axis creation",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(p1, "PLANE", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select '{p1}' (axis {axis})",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(p2, "PLANE", 0.0, 0.0, 0.0, True, 0, None, 0),
            check="truthy",
            note=f"select '{p2}' (axis {axis})",
        ),
        CallSpec(
            target="Model",
            method="InsertAxis2",
            args=(True,),
            check="truthy",
            note=f"reference axis along {axis} from two-plane intersection",
        ),
        rename_last_feature(name),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after axis creation",
        ),
    ]


def create_sketch_calls(plane_display: str) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(plane_display, "PLANE", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select plane '{plane_display}' (English-language UI names)",
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


def draw_slot_calls(
    start: tuple[float, float], end: tuple[float, float], width: float
) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateSketchSlot",
            args=(
                SW_SLOT_CREATION_LINE,
                SW_SLOT_LENGTH_CENTER_CENTER,
                width * MM_TO_M,
                start[0] * MM_TO_M,
                start[1] * MM_TO_M,
                0.0,
                end[0] * MM_TO_M,
                end[1] * MM_TO_M,
                0.0,
                0.0,
                0.0,
                0.0,
                1,
                False,
            ),
            check="non_null",
            note=f"straight slot w={width} mm from {start} to {end} mm (center-to-center)",
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


def extrude_calls(depth_mm: float, reverse: bool, name: str) -> list[CallSpec]:
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
                False,  # Flip: n/a for boss
                reverse,  # Dir: flip extrude direction
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
            remember=True,
            note=f"blind boss-extrude {depth_mm} mm{' (reversed)' if reverse else ''}",
        ),
        rename_last_feature(name),
    ]


def cut_extrude_calls(
    through_all: bool,
    depth_mm: float | None,
    reverse: bool,
    draft_angle: float | None,
    name: str,
) -> list[CallSpec]:
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
        note = f"blind cut-extrude {depth_mm} mm"
    if reverse:
        note += " (reversed)"
    draft = draft_angle is not None
    if draft:
        note += f", draft {draft_angle} deg (countersink cone)"
    return [
        *_end_sketch_edits(),
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureCut3",
            args=(
                True,  # Sd: single-ended
                False,  # Flip: remove material inside the profile
                reverse,  # Dir: flip cut direction
                t1,  # T1
                SW_END_COND_BLIND,  # T2 (unused)
                d1,  # D1
                0.0,  # D2
                draft,  # Dchk1: draft on/off
                False,  # Dchk2
                False,  # Ddir1: draft inward (cone narrows with depth)
                False,  # Ddir2
                math.radians(draft_angle) if draft_angle is not None else 0.0,  # Dang1
                0.0,  # Dang2
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
            remember=True,
            note=note,
        ),
        rename_last_feature(name),
    ]


def select_edges_calls(points_mm: list[tuple[float, float, float]]) -> list[CallSpec]:
    """Select edges by world coordinates (meters at the COM boundary)."""
    out = [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for edge-based feature",
        )
    ]
    for x, y, z in points_mm:
        out.append(
            CallSpec(
                target="Model.Extension",
                method="SelectByID2",
                args=("", "EDGE", x * MM_TO_M, y * MM_TO_M, z * MM_TO_M, True, 0, None, 0),
                check="truthy",
                note=f"select edge at ({x:.4g}, {y:.4g}, {z:.4g}) mm",
            )
        )
    return out


def fillet_calls(
    points_mm: list[tuple[float, float, float]], radius_mm: float, name: str
) -> list[CallSpec]:
    return [
        *select_edges_calls(points_mm),
        # IFeatureManager.FeatureFillet3(
        #   Options, R1, R2, Rho, Ftyp, OverflowType, ConicRhoType,
        #   Radii, Dist2Arr, RhoArr, SetBackDistances,
        #   PointRadiusArray, PointDist2Array, PointRhoArray)
        # 14 parameters: 7 scalars + 7 VARIANT arrays. None marshals as a
        # null VARIANT for the unused arrays (the recorded macro's Nothing x7).
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureFillet3",
            args=(
                SW_FILLET_DEFAULT_OPTIONS,
                radius_mm * MM_TO_M,  # R1
                0.0,  # R2
                0.0,  # Rho
                0,  # Ftyp: swFeatureFilletConstantRadius
                0,  # OverflowType: default
                0,  # ConicRhoType
                None, None, None, None, None, None, None,  # the 7 arrays
            ),
            check="non_null",
            remember=True,
            note=f"constant-radius fillet r={radius_mm} mm on {len(points_mm)} edge(s)",
        ),
        rename_last_feature(name),
    ]


def chamfer_calls(
    points_mm: list[tuple[float, float, float]],
    distance_mm: float,
    angle_deg: float,
    name: str,
) -> list[CallSpec]:
    return [
        *select_edges_calls(points_mm),
        CallSpec(
            target="Model.FeatureManager",
            method="InsertFeatureChamfer",
            args=(
                SW_CHAMFER_OPTIONS_EDGE,
                SW_CHAMFER_TYPE_ANGLE_DISTANCE,
                distance_mm * MM_TO_M,
                math.radians(angle_deg),
                0.0,
                0.0,
                0.0,
                0.0,
            ),
            check="non_null",
            remember=True,
            note=f"chamfer {distance_mm} mm x {angle_deg} deg on {len(points_mm)} edge(s)",
        ),
        rename_last_feature(name),
    ]


def _select_features_calls(feature_names: list[str]) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(fname, "BODYFEATURE", 0.0, 0.0, 0.0, True, SW_MARK_PATTERN_FEATURES, None, 0),
            check="truthy",
            note=f"select feature '{fname}' to pattern (mark 4)",
        )
        for fname in feature_names
    ]


def linear_pattern_calls(
    feature_names: list[str],
    axis_feature1: str,
    flip1: bool,
    spacing1_mm: float,
    count1: int,
    dir2: tuple[str, bool, float, int] | None,
    name: str,
) -> list[CallSpec]:
    out = [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for linear pattern",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(axis_feature1, "AXIS", 0.0, 0.0, 0.0, True, SW_MARK_PATTERN_DIRECTION1, None, 0),
            check="truthy",
            note=f"select '{axis_feature1}' as pattern direction 1 (mark 1)",
        ),
    ]
    n2, sp2, flip2 = 1, 0.0, False
    if dir2 is not None:
        axis2, flip2, sp2, n2 = dir2
        out.append(
            CallSpec(
                target="Model.Extension",
                method="SelectByID2",
                args=(axis2, "AXIS", 0.0, 0.0, 0.0, True, SW_MARK_PATTERN_DIRECTION2, None, 0),
                check="truthy",
                note=f"select '{axis2}' as pattern direction 2 (mark 2)",
            )
        )
    out += _select_features_calls(feature_names)
    out.append(
        # IFeatureManager.FeatureLinearPattern4 (SW 2015+) — 20 parameters:
        #   Num1, Spacing1, Num2, Spacing2, FlipDir1, FlipDir2,
        #   DName1, DName2, GeometryPattern, VarySketch,
        #   UseSeedGeom1, UseSeedGeom2, SeedOnly1, SeedOnly2,
        #   SpacingInstances1, SpacingInstances2 (True = define by
        #   spacing+instances), FlipRef1, FlipRef2, RefOffset1, RefOffset2
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureLinearPattern4",
            args=(
                count1,
                spacing1_mm * MM_TO_M,
                n2,
                sp2 * MM_TO_M,
                flip1,
                flip2,
                "NULL",
                "NULL",
                False,  # GeometryPattern
                False,  # VarySketch
                False, False,  # up-to-reference controls dir 1/2
                False, False,
                True, True,  # spacing+instances mode for both directions
                False, False,
                0.0, 0.0,
            ),
            check="non_null",
            remember=True,
            note=f"linear pattern {count1}x{n2} of {feature_names}",
        )
    )
    out.append(rename_last_feature(name))
    return out


def circular_pattern_calls(
    feature_names: list[str],
    axis_feature: str,
    count: int,
    total_angle_deg: float,
    equal_spacing: bool,
    name: str,
) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for circular pattern",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(axis_feature, "AXIS", 0.0, 0.0, 0.0, True, SW_MARK_PATTERN_DIRECTION1, None, 0),
            check="truthy",
            note=f"select '{axis_feature}' as pattern axis (mark 1)",
        ),
        *_select_features_calls(feature_names),
        # IFeatureManager.FeatureCircularPattern4 — 7 parameters:
        #   Number, Spacing, FlipDirection, DName, GeometryPattern,
        #   EqualSpacing, VarySketch.
        # Spacing semantics depend on EqualSpacing: True -> Spacing is the
        # TOTAL pattern angle; False -> Spacing is the PER-INSTANCE angle,
        # so convert to keep the schema's total_angle meaning (matching
        # what the ModelTracker twin validates).
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureCircularPattern4",
            args=(
                count,
                math.radians(total_angle_deg)
                if equal_spacing
                else math.radians(total_angle_deg) / max(count - 1, 1),
                False,
                "NULL",
                False,
                equal_spacing,
                False,  # VarySketch
            ),
            check="non_null",
            remember=True,
            note=f"circular pattern {count}x over {total_angle_deg} deg of {feature_names}",
        ),
        rename_last_feature(name),
    ]


def save_part_calls(path: str) -> list[CallSpec]:
    """The call plan for ``save_part``.

    SaveAs3 returns a Long status: 0 = success, nonzero = error bits
    (swFileSaveError_e) — hence ``status_zero``, not ``truthy``.

    Documented mock/real log divergence (like the new_part template
    placeholder): the COM backend resolves ``path`` to an absolute path
    before building this spec, because SolidWorks resolves relative
    paths against its own process working directory, not the caller's.
    The mock logs the path as written.
    """
    return [
        CallSpec(
            target="Model",
            method="SaveAs3",
            args=(path, SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT),
            check="status_zero",
            note="save part (silent); returns swFileSaveError_e status, 0 = success",
        ),
    ]


# --------------------------------------------------------------------------
# Drawing builders (v0.4)
# --------------------------------------------------------------------------


def get_default_drawing_template() -> CallSpec:
    return CallSpec(
        target="App",
        method="GetUserPreferenceStringValue",
        args=(SW_DEFAULT_TEMPLATE_DRAWING,),
        note="resolve default drawing template path (swDefaultTemplateDrawing)",
    )


def new_drawing_document(template: str, sheet: str) -> CallSpec:
    return CallSpec(
        target="App",
        method="NewDocument",
        args=(template, SW_PAPER_SIZES[sheet], 0.0, 0.0),
        check="non_null",
        note=f"create new drawing document ({sheet} landscape) from template",
    )


def new_drawing_calls(sheet: str, template: str = "<default drawing template>") -> list[CallSpec]:
    """The call plan for the document part of ``create_drawing``.

    Template resolution mirrors new_part: the mock logs a placeholder,
    the COM backend resolves the real path.
    """
    return [get_default_drawing_template(), new_drawing_document(template, sheet)]


def setup_sheet_calls(
    sheet: str, scale_num: int, scale_den: int, first_angle: bool, w_mm: float, h_mm: float
) -> list[CallSpec]:
    """Set paper size, scale, and projection angle on the current sheet.

    ISheet::SetProperties(PaperSize, TemplateIn, Scale1, Scale2,
    FirstAngle, Width, Height); width/height only matter for
    user-defined paper but are passed for completeness.
    """
    return [
        CallSpec(
            target="Sheet",
            method="SetProperties",
            args=(
                SW_PAPER_SIZES[sheet],
                SW_SHEET_TEMPLATES[sheet],
                float(scale_num),
                float(scale_den),
                first_angle,
                w_mm * MM_TO_M,
                h_mm * MM_TO_M,
            ),
            check="truthy",
            note=f"sheet setup: {sheet}, scale {scale_num}:{scale_den}, "
            f"{'first' if first_angle else 'third'}-angle projection",
        ),
    ]


def custom_property_calls(properties: list[tuple[str, str]]) -> list[CallSpec]:
    """Title-block custom properties, set on the *model* document.

    Standard SolidWorks sheet formats display these via $PRPSHEET links.
    ICustomPropertyManager::Add3 with swCustomPropertyDeleteAndAdd
    OVERWRITES existing values (AddCustomInfo3 would silently keep stale
    ones when a template pre-defines the field or a second drawing of the
    same model updates the metadata). Add3 returns swCustomInfoAddResult_e
    where 0 = AddedOrChanged — hence check="status_zero".
    """
    return [
        CallSpec(
            target="CustomPropertyManager",
            method="Add3",
            args=(name, SW_CUSTOM_INFO_TEXT, value, SW_CUSTOM_PROP_DELETE_AND_ADD),
            check="status_zero",
            note=f"custom property {name} = {value!r} (title block via $PRPSHEET)",
        )
        for name, value in properties
    ]


def model_view_calls(
    model_path: str,
    orientation: str,
    position_mm: tuple[float, float],
    name: str,
    scale: float | None = None,
) -> list[CallSpec]:
    """A named model view (front/isometric) at a sheet position.

    The model must be open in-session (guaranteed: the twin requires it
    saved, and saving happens in the same run). Divergence note: the COM
    backend passes the absolute model path, the mock logs it as written
    (same rule as save_part).
    """
    x, y = position_mm
    out = [
        CallSpec(
            target="Model",
            method="CreateDrawViewFromModelView3",
            args=(model_path, orientation, x * MM_TO_M, y * MM_TO_M, 0.0),
            check="non_null",
            remember=True,
            note=f"place {orientation} view of {model_path} at ({x:.4g}, {y:.4g}) mm",
        ),
    ]
    if scale is not None:
        out.append(
            CallSpec(
                target="LastFeature",
                method="ScaleDecimal",
                kind="set",
                value=scale,
                note=f"override view scale to {scale:g}",
            )
        )
    out.append(
        CallSpec(
            target="LastFeature",
            method="SetName2",
            args=(name,),
            note=f"rename view to '{name}' (twin name parity)",
        )
    )
    return out


def projected_view_calls(
    parent: str, position_mm: tuple[float, float], name: str
) -> list[CallSpec]:
    """A projected view unfolded from a parent view.

    SolidWorks applies the sheet's projection angle itself, so the same
    drop position yields the correct first- or third-angle image.
    """
    x, y = position_mm
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for projected view",
        ),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(parent, "DRAWINGVIEW", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select parent view '{parent}'",
        ),
        CallSpec(
            target="Model",
            method="CreateUnfoldedViewAt3",
            args=(x * MM_TO_M, y * MM_TO_M, 0.0, False),
            check="non_null",
            remember=True,
            note=f"projected view from '{parent}' at ({x:.4g}, {y:.4g}) mm",
        ),
        CallSpec(
            target="LastFeature",
            method="SetName2",
            args=(name,),
            note=f"rename view to '{name}' (twin name parity)",
        ),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after projected view",
        ),
    ]


def section_view_calls(
    parent: str,
    line_mm: tuple[float, float, float, float],
    label: str,
    position_mm: tuple[float, float],
    name: str,
    sheet_name: str = SW_SHEET1,
) -> list[CallSpec]:
    """A full section: cutting-line sketch in the activated parent view,
    then CreateSectionViewAt5.

    The riskiest sequence of the phase. The line is sketched with the
    parent view ACTIVATED, so its coordinates are the view's own sketch
    space — model-scale mm relative to the view origin (the projection
    of the model origin), matching the official CreateSectionViewAt5
    example — while the placement point stays in absolute sheet space.
    The line stays selected and the section call consumes it; focus then
    returns to the sheet. Divergence note: the mock logs the default
    sheet name; the COM backend passes the live one.
    """
    x1, y1, x2, y2 = line_mm
    px, py = position_mm
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for section view",
        ),
        CallSpec(
            target="Model",
            method="ActivateView",
            args=(parent,),
            check="truthy",
            note=f"activate parent view '{parent}' for the cutting line",
        ),
        CallSpec(
            target="Model.SketchManager",
            method="CreateLine",
            args=(x1 * MM_TO_M, y1 * MM_TO_M, 0.0, x2 * MM_TO_M, y2 * MM_TO_M, 0.0),
            check="non_null",
            note="cutting line through the model center (parent-view sketch "
            "coordinates: model scale, origin = projected model origin)",
        ),
        # IDrawingDoc::CreateSectionViewAt5(x, y, z, SectionLabel,
        #   Options, ExcludedComponents, SectionDepth) -> IView.
        # Options 0 = default full section; the just-sketched line is the
        # active selection.
        CallSpec(
            target="Model",
            method="CreateSectionViewAt5",
            args=(px * MM_TO_M, py * MM_TO_M, 0.0, label, 0, None, 0.0),
            check="non_null",
            remember=True,
            note=f"section view {label}-{label} from '{parent}' at "
            f"({px:.4g}, {py:.4g}) mm",
        ),
        CallSpec(
            target="LastFeature",
            method="SetName2",
            args=(name,),
            note=f"rename view to '{name}' (twin name parity)",
        ),
        CallSpec(
            target="Model",
            method="ActivateSheet",
            args=(sheet_name,),
            check="truthy",
            note=f"return focus from the view to sheet '{sheet_name}' — a "
            "silent failure here would leave later annotation picks in "
            "view coordinates",
        ),
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after section view",
        ),
    ]


def dimension_calls(
    picks_mm: list[tuple[float, float]],
    placement_mm: tuple[float, float],
    prefix: str | None,
    below: str | None,
    note: str,
) -> list[CallSpec]:
    """Select view geometry at sheet coordinates and add one dimension.

    One pick on a circle yields a diameter dimension; two picks on
    parallel edges (or an edge and a circle) yield a linear dimension —
    SolidWorks infers the type from the selection and the placement
    point. Text prefixes use SolidWorks tokens (<MOD-DIAM>, <HOLE-SPOT>,
    <HOLE-DEPTH>, <HOLE-SINK>, <MOD-DEG>).
    """
    out = [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="fresh selection for dimension",
        )
    ]
    for i, (x, y) in enumerate(picks_mm):
        out.append(
            CallSpec(
                target="Model.Extension",
                method="SelectByID2",
                args=("", "EDGE", x * MM_TO_M, y * MM_TO_M, 0.0, i > 0, 0, None, 0),
                check="truthy",
                note=f"select view edge at sheet ({x:.4g}, {y:.4g}) mm",
            )
        )
    px, py = placement_mm
    out.append(
        CallSpec(
            target="Model",
            method="AddDimension2",
            args=(px * MM_TO_M, py * MM_TO_M, 0.0),
            check="non_null",
            remember=True,
            note=note,
        )
    )
    if prefix:
        out.append(
            CallSpec(
                target="LastFeature",
                method="SetText",
                args=(SW_DIM_TEXT_PREFIX, prefix),
                note=f"dimension text prefix {prefix!r}",
            )
        )
    if below:
        out.append(
            CallSpec(
                target="LastFeature",
                method="SetText",
                args=(SW_DIM_TEXT_CALLOUT_BELOW, below),
                note=f"dimension callout below {below!r}",
            )
        )
    out.append(
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection after dimension",
        )
    )
    return out


def note_calls(text: str, position_mm: tuple[float, float]) -> list[CallSpec]:
    """A free note at a sheet position (no leader).

    Selection is cleared first because InsertNote attaches a leader to
    any selected entity.
    """
    x, y = position_mm
    return [
        CallSpec(
            target="Model",
            method="ClearSelection2",
            args=(True,),
            note="clear selection so the note gets no leader",
        ),
        CallSpec(
            target="Model",
            method="InsertNote",
            args=(text,),
            check="non_null",
            remember=True,
            note=f"sheet note {text!r}",
        ),
        CallSpec(
            target="LastFeatureAnnotation",
            method="SetPosition2",
            args=(x * MM_TO_M, y * MM_TO_M, 0.0),
            check="truthy",
            note=f"position note at ({x:.4g}, {y:.4g}) mm",
        ),
    ]


def save_drawing_calls(path: str) -> list[CallSpec]:
    """Save the drawing (same SaveAs3 semantics as save_part)."""
    return [
        CallSpec(
            target="Model",
            method="SaveAs3",
            args=(path, SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT),
            check="status_zero",
            note="save drawing (silent); returns swFileSaveError_e status, 0 = success",
        ),
    ]


# --------------------------------------------------------------------------
# Curve builders (v0.5): spline / arc / line / revolve / helix
# --------------------------------------------------------------------------


def draw_spline_calls(points: list[tuple[float, float]]) -> list[CallSpec]:
    """Interpolating spline through points (ISketchManager.CreateSpline).

    CreateSpline takes a flat (x, y, z) point array in meters. The point
    count and coordinates are exactly what the twin computed — the spline
    FIT to those points is the Windows-verified part.
    """
    flat: list[float] = []
    for x, y in points:
        flat += [x * MM_TO_M, y * MM_TO_M, 0.0]
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateSpline",
            args=(tuple(flat),),
            check="non_null",
            note=f"spline through {len(points)} points",
        ),
    ]


def draw_arc_calls(
    center: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
    ccw: bool,
) -> list[CallSpec]:
    """Circular arc (ISketchManager.CreateArc: center, start, end, dir)."""
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateArc",
            args=(
                center[0] * MM_TO_M, center[1] * MM_TO_M, 0.0,
                start[0] * MM_TO_M, start[1] * MM_TO_M, 0.0,
                end[0] * MM_TO_M, end[1] * MM_TO_M, 0.0,
                1 if ccw else -1,
            ),
            check="non_null",
            note=f"arc center {center} {'CCW' if ccw else 'CW'}",
        ),
    ]


def draw_line_calls(
    start: tuple[float, float], end: tuple[float, float]
) -> list[CallSpec]:
    return [
        CallSpec(
            target="Model.SketchManager",
            method="CreateLine",
            args=(
                start[0] * MM_TO_M, start[1] * MM_TO_M, 0.0,
                end[0] * MM_TO_M, end[1] * MM_TO_M, 0.0,
            ),
            check="non_null",
            note=f"line {start} -> {end}",
        ),
    ]


# swconst.swEndConditions_e for revolve direction types (blind angle).
SW_REVOLVE_BLIND = 0


def revolve_calls(
    axis_feature: str, angle_deg: float, reverse: bool, name: str
) -> list[CallSpec]:
    """Select a reference axis, then FeatureRevolve2 the active sketch.

    IFeatureManager.FeatureRevolve2 (20 params): a solid, non-thin,
    non-cut revolve by a blind angle about the selected axis. The two
    OffsetDistance doubles (positions 13-14, after OffsetReverse1/2) are
    part of the documented signature — omitting them shifts every later
    argument and the call fails.
    """
    return [
        *_end_sketch_edits(),
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(axis_feature, "AXIS", 0.0, 0.0, 0.0, True, 0, None, 0),
            check="truthy",
            note=f"select '{axis_feature}' as the revolve axis",
        ),
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureRevolve2",
            args=(
                True,   # SingleDir
                True,   # IsSolid
                False,  # IsThin
                False,  # IsCut
                reverse,  # ReverseDir
                False,  # BothDirectionUpToSameEntity
                SW_REVOLVE_BLIND,  # Dir1Type
                SW_REVOLVE_BLIND,  # Dir2Type
                math.radians(angle_deg),  # Dir1Angle
                0.0,    # Dir2Angle
                False, False,  # OffsetReverse1/2
                0.0, 0.0,  # OffsetDistance1/2
                0,      # ThinType
                0.0, 0.0,  # ThinThickness1/2
                True,   # Merge
                False,  # UseFeatScope
                True,   # UseAutoSelect
            ),
            check="non_null",
            remember=True,
            note=f"revolve {angle_deg}° about {axis_feature}",
        ),
        rename_last_feature(name),
    ]


def helix_thread_calls(
    diameter_mm: float,
    pitch_mm: float,
    length_mm: float,
    right_handed: bool,
    revolutions: float,
    name: str,
    plane_display: str = "Front Plane",
) -> list[CallSpec]:
    """A cosmetic helix guide curve (least-verified surface of v0.5).

    Opens a sketch on a plane, draws the base circle at the thread
    diameter, CLOSES the sketch (leaving it selected), then builds the
    helix with IModelDoc2.InsertHelix (by pitch & revolutions) consuming
    that selected circle. This emits only the helix CURVE — the visible
    triangular thread rib would be a separate profile + SweepFeature,
    which is deferred: the whole helix/sweep surface is Windows-verified
    and cosmetic, never load-bearing. The 3rd InsertHelix argument is
    Clockwise (mapped from right_handed); confirm the handedness live.
    """
    return [
        # open a sketch for the base circle
        CallSpec(
            target="Model.Extension",
            method="SelectByID2",
            args=(plane_display, "PLANE", 0.0, 0.0, 0.0, False, 0, None, 0),
            check="truthy",
            note=f"select plane '{plane_display}' for the helix base circle",
        ),
        CallSpec(
            target="Model.SketchManager",
            method="InsertSketch",
            args=(True,),
            note="open the helix base-circle sketch",
        ),
        CallSpec(
            target="Model.SketchManager",
            method="CreateCircleByRadius",
            args=(0.0, 0.0, 0.0, (diameter_mm / 2.0) * MM_TO_M),
            check="non_null",
            note=f"helix base circle Ø{diameter_mm} mm",
        ),
        # close the sketch (toggle out of sketch mode) — it stays selected,
        # and InsertHelix consumes the selected circle. Do NOT ClearSelection.
        CallSpec(
            target="Model.SketchManager",
            method="InsertSketch",
            args=(True,),
            note="close the base-circle sketch (stays selected for InsertHelix)",
        ),
        # IModelDoc2.InsertHelix(ByPitchAndRevolution, ReverseDirection,
        #   Clockwise, TaperHelix, TaperAngle, Height, Pitch, Revolution,
        #   StartAngle) — cosmetic; exact form/handedness Windows-verified.
        CallSpec(
            target="Model",
            method="InsertHelix",
            args=(
                True,            # by pitch & revolutions
                False,           # reverse direction
                not right_handed,  # Clockwise (RH helix is CCW)
                False,           # taper
                0.0,             # taper angle
                length_mm * MM_TO_M,
                pitch_mm * MM_TO_M,
                revolutions,
                0.0,             # start angle
            ),
            check="non_null",
            remember=True,
            note=f"cosmetic helix curve: pitch {pitch_mm} mm × {revolutions:.2f} rev "
            f"({'RH' if right_handed else 'LH'}) — Windows-verified; rib sweep deferred",
        ),
        rename_last_feature(name),
    ]


def finalize_calls() -> list[CallSpec]:
    return [
        CallSpec(
            target="Model",
            method="ViewZoomtofit2",
            note="zoom to fit for visibility",
        ),
    ]
