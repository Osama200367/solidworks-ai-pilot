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
# swconst.swSaveAsVersion_e / swconst.swSaveAsOptions_e
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_AS_OPTIONS_SILENT = 1
# swconst.swRefPlaneReferenceConstraints_e
SW_REF_PLANE_DISTANCE = 8
# Flip option for offset reference planes. NOTE: this enum value is the
# least-certain constant in this module — verify on the first Windows
# smoke test that a negative-offset plane lands on the correct side.
SW_REF_PLANE_OPTION_FLIP = 512
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
        CallSpec(
            target="Model.FeatureManager",
            method="InsertRefPlane",
            args=(options, abs(distance_mm) * MM_TO_M, 0, 0.0, 0, 0.0),
            check="non_null",
            remember=True,
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
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureFillet3",
            args=(
                SW_FILLET_DEFAULT_OPTIONS,
                radius_mm * MM_TO_M,
                0.0,  # Rho (conic; unused)
                0,  # setback distances count
                0,  # PointRadiusArray (VARIANT; none)
                0,  # SetBackDistanceArray (VARIANT; none)
                0,  # PointArray (VARIANT; none)
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
        # IFeatureManager.FeatureLinearPattern4(
        #   Num1, Spacing1, Num2, Spacing2, FlipDir1, FlipDir2,
        #   DName1, DName2, GeometryPattern, VarySketch)
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
                False,
                False,
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
        # IFeatureManager.FeatureCircularPattern4(
        #   Number, Spacing, FlipDirection, DName, GeometryPattern, EqualSpacing)
        CallSpec(
            target="Model.FeatureManager",
            method="FeatureCircularPattern4",
            args=(
                count,
                math.radians(total_angle_deg),
                False,
                "NULL",
                False,
                equal_spacing,
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


def finalize_calls() -> list[CallSpec]:
    return [
        CallSpec(
            target="Model",
            method="ViewZoomtofit2",
            note="zoom to fit for visibility",
        ),
    ]
