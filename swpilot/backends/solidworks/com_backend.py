"""SolidWorks COM backend (pywin32 dynamic dispatch).

Executes the exact :class:`CallSpec` sequences produced by
``swpilot.backends.calls`` — the same specs the mock backend logs — so a
call log validated in CI is precisely what runs here.

Cannot be executed in CI by design; smoke-test on Windows per
WINDOWS_SETUP.md.
"""

from __future__ import annotations

import os
from typing import Any

from swpilot.backends import calls
from swpilot.backends.base import Backend, BackendError
from swpilot.backends.calls import CallSpec
from swpilot.commands.schema import PlaneName

try:
    import win32com.client  # noqa: F401
except ImportError as _exc:  # pragma: no cover - exercised only off-Windows
    raise ImportError(
        "The SolidWorks backend needs pywin32 on Windows: "
        "pip install 'swpilot[windows]'"
    ) from _exc


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
        self._part_template = part_template or os.environ.get("SWPILOT_PART_TEMPLATE")

    # -- CallSpec execution --------------------------------------------

    def _resolve_target(self, target: str) -> Any:
        root_name, _, rest = target.partition(".")
        if root_name == "App":
            obj = self._app
        elif root_name == "Model":
            if self._model is None:
                raise BackendError(f"internal error: no open model for call target {target!r}")
            obj = self._model
        else:
            raise BackendError(f"internal error: unknown call target root {root_name!r}")
        if rest:
            for attr in rest.split("."):
                obj = getattr(obj, attr)
        return obj

    def _execute(self, spec: CallSpec) -> Any:
        obj = self._resolve_target(spec.target)
        try:
            if spec.kind == "set":
                setattr(obj, spec.method, spec.value)
                result = None
            else:
                result = getattr(obj, spec.method)(*spec.args)
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
            raise BackendError(
                f"COM call {spec.target}.{spec.method} returned nothing: {spec.note}. "
                "SolidWorks likely rejected the operation; check the part for "
                "error markers."
            )
        self.call_log.append(spec)
        return result

    def _execute_all(self, specs: list[CallSpec]) -> None:
        for spec in specs:
            self._execute(spec)

    # -- primitive operations ------------------------------------------

    def new_part(self) -> None:
        if self._model is not None:
            raise BackendError("new_part: a part is already open; v0.1 supports one part per run")
        template = self._part_template
        if template is None:
            template = self._execute(calls.get_default_part_template())
            if not template:
                raise BackendError(
                    "new_part: SolidWorks returned no default part template; set one in "
                    "Tools > Options > Default Templates, or pass --template / set "
                    "SWPILOT_PART_TEMPLATE"
                )
        self._model = self._execute(calls.new_document(str(template)))

    def create_sketch(self, plane: PlaneName) -> None:
        self._require_model("create_sketch")
        self._execute_all(calls.create_sketch_calls(plane))

    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None:
        self._require_model("draw_rectangle")
        self._execute_all(calls.draw_rectangle_calls(center, width, height))

    def draw_circle(self, center: tuple[float, float], diameter: float) -> None:
        self._require_model("draw_circle")
        self._execute_all(calls.draw_circle_calls(center, diameter))

    def extrude(self, depth: float) -> None:
        self._require_model("extrude")
        self._execute_all(calls.extrude_calls(depth))

    def cut_extrude(self, through_all: bool, depth: float | None) -> None:
        self._require_model("cut_extrude")
        self._execute_all(calls.cut_extrude_calls(through_all, depth))

    def save_part(self, path: str) -> None:
        self._require_model("save_part")
        abs_path = os.path.abspath(path)
        self._execute_all(calls.save_part_calls(abs_path))

    # -- lifecycle / reporting -----------------------------------------

    def finalize(self) -> None:
        if self._model is not None:
            self._execute_all(calls.finalize_calls())

    def close(self) -> None:
        # Drop COM references; leave the SolidWorks application running —
        # killing an app the user may have had open would be hostile.
        self._model = None
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
