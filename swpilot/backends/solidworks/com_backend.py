# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""SolidWorks COM backend (pywin32 dynamic dispatch).

Executes the exact :class:`CallSpec` sequences produced by
``swpilot.backends.calls`` — the same specs the mock backend logs — so a
call log validated in CI is precisely what runs here. The executor's
shared ModelTracker has already validated every command and resolved
selections before any method here is called.

Cannot be executed in CI by design; smoke-test on Windows per
WINDOWS_SETUP.md.
"""

from __future__ import annotations

import os
from typing import Any

from swpilot.backends import calls
from swpilot.backends.base import Backend, BackendError, Vec3
from swpilot.backends.calls import CallSpec
from swpilot.model.drawing import DimSpec, DrawingSetup, NoteSpec, SectionSpec, ViewSpec

try:
    import win32com.client  # noqa: F401
except ImportError as _exc:  # pragma: no cover - exercised only off-Windows
    raise ImportError(
        "The SolidWorks backend needs pywin32 on Windows: "
        "pip install 'swpilot[windows]'"
    ) from _exc


# Methods whose trailing parameters are ByRef long out-params: the logged
# CallSpec carries plain 0 placeholders; _execute swaps in VT_BYREF VARIANTs.
_BYREF_TRAILING: dict[str, int] = {
    "AddMate5": 1,  # ErrorStatus
    "ActivateDoc2": 1,  # Errors
    "OpenDoc6": 2,  # Errors, Warnings
}


class SolidWorksBackend(Backend):
    name = "solidworks"

    def __init__(self, visible: bool = True, part_template: str | None = None) -> None:
        super().__init__()
        import win32com.client

        try:
            # Dispatch attaches to a running SolidWorks instance or starts one.
            self._app = win32com.client.Dispatch("SldWorks.Application")
        except Exception as exc:  # pywin32 raises pywintypes.com_error
            raise BackendError(
                f"could not connect to SolidWorks via COM: {exc}. "
                "Is SolidWorks installed on this machine?"
            ) from exc
        self._app.Visible = visible
        self._model: Any = None
        self._last_feature: Any = None
        self._part_template = part_template or os.environ.get("SWPILOT_PART_TEMPLATE")
        self._assembly_template = os.environ.get("SWPILOT_ASSEMBLY_TEMPLATE")
        self._drawing_template = os.environ.get("SWPILOT_DRAWING_TEMPLATE")
        self._documents: dict[str, Any] = {}  # logical name -> model handle
        self._titles: dict[str, str] = {}  # logical name -> window title
        self._components: dict[str, Any] = {}  # instance name -> IComponent2

    # -- CallSpec execution --------------------------------------------

    def _resolve_target(self, target: str) -> Any:
        root_name, _, rest = target.partition(".")
        if root_name == "App":
            obj = self._app
        elif root_name == "Model":
            if self._model is None:
                raise BackendError(f"internal error: no open model for call target {target!r}")
            obj = self._model
        elif root_name == "LastFeature":
            obj = self._last_feature
            if obj is None:
                # Fallback for creators that return a bool (e.g. InsertAxis2):
                # the newest feature is at position 0 from the tree's end.
                obj = self._model.FeatureByPositionReverse(0)
            if obj is None:
                raise BackendError(
                    "internal error: no feature available to rename (LastFeature)"
                )
        elif root_name == "LastFeatureAnnotation":
            if self._last_feature is None:
                raise BackendError(
                    "internal error: no annotation available (LastFeatureAnnotation)"
                )
            obj = self._last_feature.GetAnnotation()
            if obj is None:
                raise BackendError(
                    "internal error: the last annotation object returned no IAnnotation"
                )
        elif root_name == "Sheet":
            if self._model is None:
                raise BackendError(f"internal error: no open model for call target {target!r}")
            obj = self._model.GetCurrentSheet()
            if obj is None:
                raise BackendError("internal error: the drawing has no current sheet")
        elif root_name == "CustomPropertyManager":
            if self._model is None:
                raise BackendError(f"internal error: no open model for call target {target!r}")
            # Parameterized property: the empty string selects the
            # document-level (configuration-independent) property set.
            obj = self._model.Extension.CustomPropertyManager("")
            if obj is None:
                raise BackendError(
                    "internal error: the document returned no custom property manager"
                )
        elif root_name.startswith("Component:") or (
            root_name == "Component" and rest
        ):
            comp_name = target.partition(":")[2]
            obj = self._components.get(comp_name)
            if obj is None:
                raise BackendError(
                    f"internal error: unknown component handle {comp_name!r}"
                )
            return obj
        else:
            raise BackendError(f"internal error: unknown call target root {root_name!r}")
        if rest:
            for attr in rest.split("."):
                obj = getattr(obj, attr)
        return obj

    def _execute(self, spec: CallSpec) -> Any:
        try:
            # Resolution stays inside the try: a dead/disconnected COM object
            # can fail on attribute access, not just on the final call.
            obj = self._resolve_target(spec.target)
            if spec.kind == "set":
                value = spec.value
                if spec.method == "Transform2" and spec.target.startswith("Component:"):
                    # The logged value is 16 floats; the live property needs
                    # an IMathTransform built from them.
                    mu = self._app.GetMathUtility()
                    assert isinstance(spec.value, tuple)
                    value = mu.CreateTransform(list(spec.value))
                setattr(obj, spec.method, value)
                result = None
            else:
                live_args = spec.args
                if spec.method == "CreateSpline":
                    # ISketchManager.CreateSpline wants a SAFEARRAY of doubles
                    # (VT_ARRAY|VT_R8). Under late binding a bare Python
                    # tuple of floats marshals as VT_ARRAY|VT_VARIANT, which
                    # SolidWorks rejects (returns Nothing) — so build a typed
                    # double array VARIANT, like the Transform2 / ByRef cases.
                    import pythoncom
                    import win32com.client as w32

                    assert isinstance(spec.args[0], tuple)
                    live_args = (
                        w32.VARIANT(
                            pythoncom.VT_ARRAY | pythoncom.VT_R8, list(spec.args[0])
                        ),
                    )
                byref_n = _BYREF_TRAILING.get(spec.method, 0)
                byref_vars = []
                if byref_n:
                    # ByRef long out-params must be passed as VT_BYREF
                    # VARIANTs under late binding; the logged spec keeps the
                    # plain 0 placeholders (documented divergence, like
                    # template paths).
                    import pythoncom
                    import win32com.client as w32

                    byref_vars = [
                        w32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                        for _ in range(byref_n)
                    ]
                    live_args = spec.args[:-byref_n] + tuple(byref_vars)
                result = getattr(obj, spec.method)(*live_args)
                if isinstance(result, tuple):
                    # Early-bound dispatch returns out-params alongside the
                    # result; the primary result is always first.
                    result = result[0] if result else None
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"COM call {spec.target}.{spec.method} failed: {exc} ({spec.note})"
            ) from exc
        if spec.check == "truthy" and not result:
            raise BackendError(
                f"COM call {spec.target}.{spec.method} reported failure "
                f"(returned {result!r}): {spec.note}"
            )
        if spec.check == "non_null" and result is None:
            detail = ""
            if spec.kind == "call" and _BYREF_TRAILING.get(spec.method):
                values = [getattr(v, "value", None) for v in byref_vars]
                detail = f" (out-param status: {values})"
            raise BackendError(
                f"COM call {spec.target}.{spec.method} returned nothing: {spec.note}."
                f"{detail} SolidWorks likely rejected the operation; check the "
                "part for error markers."
            )
        if spec.check == "status_zero" and result not in (0, None):
            raise BackendError(
                f"COM call {spec.target}.{spec.method} returned error status "
                f"{result!r} (swFileSaveError_e bits): {spec.note}"
            )
        if spec.remember:
            self._last_feature = result
        if spec.target == "LastFeature" and spec.method in ("Name", "Name2", "SetName2"):
            # A rename ends the object's LastFeature lifetime, so later
            # remember=False creators fall back to FeatureByPositionReverse.
            # Non-rename LastFeature specs (ScaleDecimal, SetText) keep it
            # alive — renames are always last in each builder's sequence.
            self._last_feature = None
        self.call_log.append(spec)
        return result

    def _execute_all(self, specs: list[CallSpec]) -> None:
        # Each builder sequence is self-contained: a stale remembered object
        # from an earlier batch (e.g. a DisplayDimension with no trailing
        # rename) must never satisfy this batch's LastFeature lookup.
        self._last_feature = None
        for spec in specs:
            self._execute(spec)

    # -- document lifecycle --------------------------------------------

    def _register_document(self, name: str, model: Any) -> None:
        self._documents[name] = model
        try:
            self._titles[name] = str(model.GetTitle())
        except Exception:
            self._titles[name] = name
        self._model = model
        self._active_doc = name

    def new_part(self, name: str) -> None:
        template = self._part_template
        if template is None:
            template = self._execute(calls.get_default_part_template())
            if not template:
                raise BackendError(
                    "new_part: SolidWorks returned no default part template; set one in "
                    "Tools > Options > Default Templates, or pass --template / set "
                    "SWPILOT_PART_TEMPLATE"
                )
        model = self._execute(calls.new_document(str(template)))
        self._register_document(name, model)

    def new_assembly(self, name: str) -> None:
        template = self._assembly_template
        specs = calls.new_assembly_calls()
        if template is None:
            template = self._execute(specs[0])
            if not template:
                raise BackendError(
                    "new_assembly: SolidWorks returned no default assembly template; "
                    "set one in Tools > Options > Default Templates, or set "
                    "SWPILOT_ASSEMBLY_TEMPLATE"
                )
        model = self._execute(calls.new_assembly_document(str(template)))
        self._register_document(name, model)

    def _current_title(self, name: str) -> str:
        """The document's live window title (SaveAs3 changes titles)."""
        handle = self._documents.get(name)
        if handle is None:
            raise BackendError(f"activate_document: unknown document {name!r}")
        try:
            title = str(handle.GetTitle())
            self._titles[name] = title
            return title
        except Exception:
            return self._titles.get(name, name)

    def activate_document(self, name: str, kind: str) -> None:
        title = self._current_title(name)
        (spec,) = calls.activate_document_calls(title)
        model = self._execute(spec)
        self._documents[name] = model
        self._model = model
        self._active_doc = name

    # -- assembly operations -------------------------------------------

    def insert_component(
        self,
        path: str,
        name: str,
        translation: tuple[float, float, float],
        rotation_row_major: list[float] | None,
        fixed: bool,
        external: bool,
    ) -> None:
        self._require_model("insert_component")
        abs_path = os.path.abspath(path)
        if external:
            # External file: AddComponent5 needs the document open in the
            # session. Load it, then re-activate the assembly (OpenDoc6
            # makes the opened part active). Gated on the schema-level
            # `external` flag — the same predicate the mock logs from —
            # so the two call plans cannot diverge structurally; OpenDoc6
            # on an already-open file harmlessly returns the open document.
            self._execute_all(calls.open_external_part_calls(abs_path))
            assert self._active_doc is not None
            (reactivate,) = calls.activate_document_calls(
                self._current_title(self._active_doc)
            )
            self._model = self._execute(reactivate)
            self._documents[self._active_doc] = self._model
        insert_spec, rename_spec = calls.insert_component_calls(abs_path, name, translation)
        component = self._execute(insert_spec)
        self._components[name] = component
        self._execute(rename_spec)
        if rotation_row_major is not None:
            self._execute_all(
                calls.component_transform_calls(name, rotation_row_major, translation)
            )
        if fixed:
            asm_title = self._titles.get(self._active_doc or "", "<asm>")
            self._execute_all(calls.fix_component_calls(name, asm_title))

    def add_mate(
        self,
        mate_type: str,
        pick_a: Vec3,
        pick_b: Vec3,
        value: float | None,
        name: str,
    ) -> None:
        self._require_model("add_mate")
        self._execute_all(calls.add_mate_calls(mate_type, pick_a, pick_b, value, name))

    def save_assembly(self, path: str) -> None:
        self._require_model("save_assembly")
        abs_path = os.path.abspath(path)
        self._execute_all(calls.save_assembly_calls(abs_path))
        if self._active_doc is not None:
            self._current_title(self._active_doc)  # refresh: saving retitles

    # -- primitive operations ------------------------------------------

    def create_plane(self, name: str, base_display: str, distance: float) -> None:
        self._require_model("create_plane")
        self._execute_all(calls.create_plane_calls(name, base_display, distance))

    def create_axis(self, axis: str, name: str) -> None:
        self._require_model("create_axis")
        self._execute_all(calls.create_axis_calls(axis, name))

    def create_sketch(self, plane_display: str) -> None:
        self._require_model("create_sketch")
        self._execute_all(calls.create_sketch_calls(plane_display))

    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None:
        self._require_model("draw_rectangle")
        self._execute_all(calls.draw_rectangle_calls(center, width, height))

    def draw_circle(self, center: tuple[float, float], diameter: float) -> None:
        self._require_model("draw_circle")
        self._execute_all(calls.draw_circle_calls(center, diameter))

    def draw_slot(
        self, start: tuple[float, float], end: tuple[float, float], width: float
    ) -> None:
        self._require_model("draw_slot")
        self._execute_all(calls.draw_slot_calls(start, end, width))

    def extrude(self, depth: float, reverse: bool, name: str) -> None:
        self._require_model("extrude")
        self._execute_all(calls.extrude_calls(depth, reverse, name))

    def cut_extrude(
        self,
        through_all: bool,
        depth: float | None,
        reverse: bool,
        draft_angle: float | None,
        name: str,
    ) -> None:
        self._require_model("cut_extrude")
        self._execute_all(
            calls.cut_extrude_calls(through_all, depth, reverse, draft_angle, name)
        )

    def fillet(self, edge_points: list[Vec3], radius: float, name: str) -> None:
        self._require_model("fillet")
        self._execute_all(calls.fillet_calls(edge_points, radius, name))

    def chamfer(
        self, edge_points: list[Vec3], distance: float, angle: float, name: str
    ) -> None:
        self._require_model("chamfer")
        self._execute_all(calls.chamfer_calls(edge_points, distance, angle, name))

    def linear_pattern(
        self,
        feature_names: list[str],
        axis_feature1: str,
        flip1: bool,
        spacing1: float,
        count1: int,
        dir2: tuple[str, bool, float, int] | None,
        name: str,
    ) -> None:
        self._require_model("linear_pattern")
        self._execute_all(
            calls.linear_pattern_calls(
                feature_names, axis_feature1, flip1, spacing1, count1, dir2, name
            )
        )

    def circular_pattern(
        self,
        feature_names: list[str],
        axis_feature: str,
        count: int,
        total_angle: float,
        equal_spacing: bool,
        name: str,
    ) -> None:
        self._require_model("circular_pattern")
        self._execute_all(
            calls.circular_pattern_calls(
                feature_names, axis_feature, count, total_angle, equal_spacing, name
            )
        )

    def save_part(self, path: str) -> None:
        self._require_model("save_part")
        abs_path = os.path.abspath(path)
        self._execute_all(calls.save_part_calls(abs_path))
        if self._active_doc is not None:
            self._current_title(self._active_doc)  # refresh: saving retitles

    # -- curve operations (v0.5) ---------------------------------------

    def draw_spline(self, points: list[tuple[float, float]]) -> None:
        self._require_model("draw_spline")
        self._execute_all(calls.draw_spline_calls(points))

    def draw_arc(
        self,
        center: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
        ccw: bool,
    ) -> None:
        self._require_model("draw_arc")
        self._execute_all(calls.draw_arc_calls(center, start, end, ccw))

    def draw_line(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        self._require_model("draw_line")
        self._execute_all(calls.draw_line_calls(start, end))

    def revolve(self, axis_feature: str, angle: float, reverse: bool, name: str) -> None:
        self._require_model("revolve")
        self._execute_all(calls.revolve_calls(axis_feature, angle, reverse, name))

    def helix_thread(
        self,
        diameter: float,
        pitch: float,
        length: float,
        right_handed: bool,
        revolutions: float,
        name: str,
    ) -> None:
        self._require_model("helix_thread")
        self._execute_all(
            calls.helix_thread_calls(diameter, pitch, length, right_handed, revolutions, name)
        )

    # -- drawing operations (v0.4) -------------------------------------

    def create_drawing(self, setup: DrawingSetup) -> None:
        # Title-block custom properties live on the model document.
        if self._active_doc != setup.model_doc:
            title = self._current_title(setup.model_doc)
            (spec,) = calls.activate_document_calls(title)
            self._model = self._execute(spec)
            self._documents[setup.model_doc] = self._model
            self._active_doc = setup.model_doc
        self._require_model("create_drawing")
        self._execute_all(calls.custom_property_calls(setup.properties))
        template = self._drawing_template
        if template is None:
            template = self._execute(calls.get_default_drawing_template())
            if not template:
                raise BackendError(
                    "create_drawing: SolidWorks returned no default drawing template; "
                    "set one in Tools > Options > Default Templates, or set "
                    "SWPILOT_DRAWING_TEMPLATE"
                )
        model = self._execute(calls.new_drawing_document(str(template), setup.sheet))
        self._register_document(setup.name, model)
        self._execute_all(
            calls.setup_sheet_calls(
                setup.sheet,
                setup.scale[0],
                setup.scale[1],
                setup.first_angle,
                setup.paper_w,
                setup.paper_h,
            )
        )
        self._execute_all(
            calls.note_calls(setup.units_note_text, setup.units_note_position)
        )

    def add_views(self, views: list[ViewSpec]) -> None:
        self._require_model("add_views")
        for v in views:
            if v.method == "model":
                assert v.model_path is not None and v.orientation is not None
                self._execute_all(
                    calls.model_view_calls(
                        os.path.abspath(v.model_path),
                        v.orientation,
                        v.position,
                        v.name,
                        v.scale,
                    )
                )
            else:
                assert v.parent is not None
                self._execute_all(
                    calls.projected_view_calls(v.parent, v.position, v.name)
                )

    def _live_sheet_name(self) -> str:
        """The current sheet's real name (templates may not use 'Sheet1')."""
        try:
            sheet = self._model.GetCurrentSheet()
            raw = sheet.GetName
            name = raw() if callable(raw) else raw
            return str(name) if name else calls.SW_SHEET1
        except Exception:
            return calls.SW_SHEET1

    def add_section_view(self, spec: SectionSpec) -> None:
        self._require_model("add_section_view")
        # Resolve the sheet name while the model is still in sheet mode:
        # a wrong name would make ActivateSheet fail and strand later
        # annotation picks in view coordinates.
        self._execute_all(
            calls.section_view_calls(
                spec.parent,
                spec.line,
                spec.label,
                spec.position,
                spec.name,
                sheet_name=self._live_sheet_name(),
            )
        )

    def add_annotations(self, dims: list[DimSpec], notes: list[NoteSpec]) -> None:
        self._require_model("add_annotations")
        for d in dims:
            self._execute_all(
                calls.dimension_calls(
                    d.picks,
                    d.placement,
                    d.prefix,
                    d.below,
                    f"{d.kind} dimension '{d.name}' = {d.value:g} mm in view '{d.view}'",
                )
            )
        for n in notes:
            self._execute_all(calls.note_calls(n.text, n.position))

    def save_drawing(self, path: str) -> None:
        self._require_model("save_drawing")
        abs_path = os.path.abspath(path)
        self._execute_all(calls.save_drawing_calls(abs_path))
        if self._active_doc is not None:
            self._current_title(self._active_doc)  # refresh: saving retitles

    # -- lifecycle / reporting -----------------------------------------

    def finalize(self) -> None:
        if self._model is not None:
            self._execute_all(calls.finalize_calls())

    def close(self) -> None:
        # Drop COM references; leave the SolidWorks application running —
        # killing an app the user may have had open would be hostile.
        self._model = None
        self._last_feature = None
        self._documents.clear()
        self._titles.clear()
        self._components.clear()
        self._app = None

    def state_summary(self) -> dict[str, object]:
        if self._model is None:
            return {"part": None}
        try:
            feature_names: list[str] = []
            feat = self._model.FirstFeature()
            while feat is not None:
                feature_names.append(str(feat.Name))
                feat = feat.GetNextFeature()
            return {"title": str(self._model.GetTitle()), "features": feature_names}
        except Exception as exc:
            return {"error": f"could not read feature tree: {exc}"}

    def _require_model(self, op: str) -> None:
        if self._model is None:
            raise BackendError(f"{op}: no part is open; start with new_part (or create_plate)")
