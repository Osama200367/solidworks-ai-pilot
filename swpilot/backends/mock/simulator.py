"""Stateful mock backend: validates commands and logs the COM call plan.

Every operation appends the exact :class:`CallSpec` sequence the COM
backend would execute (built by the shared ``swpilot.backends.calls``
module), then updates and validates an in-memory model. Validation
failures raise :class:`BackendError` with actionable messages — this is
what lets CI catch "commands parse but make no geometric sense".
"""

from __future__ import annotations

from swpilot.backends import calls
from swpilot.backends.base import Backend, BackendError
from swpilot.backends.mock.geometry import Circle, Rect, Shape, contains, valid_contour_pair
from swpilot.backends.mock.model import Feature, PartModel, Sketch
from swpilot.commands.schema import PlaneName


class MockBackend(Backend):
    name = "mock"

    def __init__(self) -> None:
        super().__init__()
        self.model: PartModel | None = None

    # -- helpers -------------------------------------------------------

    def _require_part(self, op: str) -> PartModel:
        if self.model is None:
            raise BackendError(f"{op}: no part is open; start with new_part (or create_plate)")
        return self.model

    def _require_active_sketch(self, op: str) -> tuple[PartModel, Sketch]:
        model = self._require_part(op)
        if model.active_sketch is None:
            raise BackendError(f"{op}: no active sketch; use create_sketch first")
        return model, model.active_sketch

    def _add_entity(self, shape: Shape, op: str) -> None:
        _, sketch = self._require_active_sketch(op)
        for existing in sketch.entities:
            if not valid_contour_pair(existing, shape):
                raise BackendError(
                    f"{op}: new contour overlaps or touches an existing contour in "
                    f"{sketch.name}; SolidWorks rejects intersecting/tangent contours "
                    "in a single feature sketch (self-intersecting or zero-thickness "
                    "geometry)"
                )
        sketch.entities.append(shape)

    def _consume_active_sketch(self, op: str) -> tuple[PartModel, Sketch]:
        model, sketch = self._require_active_sketch(op)
        if not sketch.entities:
            raise BackendError(f"{op}: active sketch {sketch.name} is empty; draw something first")
        model.active_sketch = None
        return model, sketch

    # -- primitive operations ------------------------------------------

    def new_part(self) -> None:
        if self.model is not None:
            raise BackendError(
                "new_part: a part is already open; v0.1 supports one part per run"
            )
        self.call_log.extend(calls.new_part_calls())
        self.model = PartModel()

    def create_sketch(self, plane: PlaneName) -> None:
        model = self._require_part("create_sketch")
        if model.active_sketch is not None:
            raise BackendError(
                f"create_sketch: sketch {model.active_sketch.name} is still active; "
                "consume it with extrude/cut_extrude before opening another sketch"
            )
        self.call_log.extend(calls.create_sketch_calls(plane))
        sketch = Sketch(name=model.next_sketch_name(), plane=plane)
        model.sketches.append(sketch)
        model.active_sketch = sketch

    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None:
        self._add_entity(Rect(center[0], center[1], width, height), "draw_rectangle")
        self.call_log.extend(calls.draw_rectangle_calls(center, width, height))

    def draw_circle(self, center: tuple[float, float], diameter: float) -> None:
        self._add_entity(Circle(center[0], center[1], diameter), "draw_circle")
        self.call_log.extend(calls.draw_circle_calls(center, diameter))

    def extrude(self, depth: float) -> None:
        model, sketch = self._consume_active_sketch("extrude")
        self.call_log.extend(calls.extrude_calls(depth))
        feature = Feature(
            name=model.next_feature_name("boss"),
            kind="boss",
            sketch=sketch,
            depth_mm=depth,
        )
        sketch.consumed_by = feature.name
        model.features.append(feature)

    def cut_extrude(self, through_all: bool, depth: float | None) -> None:
        model, sketch = self._require_active_sketch("cut_extrude")
        if not model.has_solid:
            raise BackendError(
                "cut_extrude: there is no solid material to cut; extrude a base "
                "feature first"
            )
        if not sketch.entities:
            raise BackendError(
                f"cut_extrude: active sketch {sketch.name} is empty; draw something first"
            )
        self._validate_cut_inside_material(model, sketch)
        model.active_sketch = None
        self.call_log.extend(calls.cut_extrude_calls(through_all, depth))
        feature = Feature(
            name=model.next_feature_name("cut"),
            kind="cut",
            sketch=sketch,
            depth_mm=depth,
            through_all=through_all,
        )
        sketch.consumed_by = feature.name
        model.features.append(feature)

    def _validate_cut_inside_material(self, model: PartModel, sketch: Sketch) -> None:
        footprints = model.footprints(sketch.plane)
        if not footprints:
            self._warn(
                f"cut_extrude: no boss feature was sketched on the '{sketch.plane}' "
                "plane, so the simulator cannot validate that the cut lands inside "
                "material (cross-plane containment is not checked in v0.1)"
            )
            return
        for shape in sketch.entities:
            if not any(contains(outer, shape) for outer in footprints):
                raise BackendError(
                    f"cut_extrude: contour {_describe(shape)} in {sketch.name} is not "
                    "strictly inside the existing material footprint; the cut would "
                    "miss the part or leave zero-thickness geometry at an edge"
                )

    def save_part(self, path: str) -> None:
        model = self._require_part("save_part")
        if model.active_sketch is not None:
            self._warn(
                f"save_part: sketch {model.active_sketch.name} is still open; "
                "the part will be saved mid-edit"
            )
        if not model.has_solid:
            self._warn("save_part: the part has no solid geometry yet")
        self.call_log.extend(calls.save_part_calls(path))
        model.saved_to.append(path)

    # -- lifecycle / reporting -----------------------------------------

    def finalize(self) -> None:
        if self.model is None:
            return
        for sketch in self.model.sketches:
            if sketch.consumed_by is None:
                self._warn(f"run finished with unconsumed sketch {sketch.name}")
        self.call_log.extend(calls.finalize_calls())

    def state_summary(self) -> dict[str, object]:
        if self.model is None:
            return {"part": None}
        return self.model.summary()


def _describe(shape: Shape) -> str:
    if isinstance(shape, Circle):
        return f"circle d={shape.diameter} mm at ({shape.cx}, {shape.cy})"
    return f"rectangle {shape.width}x{shape.height} mm at ({shape.cx}, {shape.cy})"
