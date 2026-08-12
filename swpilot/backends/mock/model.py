"""In-memory model tree for the mock backend."""

from __future__ import annotations

from dataclasses import dataclass, field

from swpilot.backends.mock.geometry import Circle, Rect, Shape
from swpilot.commands.schema import PlaneName


@dataclass
class Sketch:
    name: str  # mirrors SolidWorks naming: Sketch1, Sketch2, ...
    plane: PlaneName
    entities: list[Shape] = field(default_factory=list)
    consumed_by: str | None = None  # feature name, once used


@dataclass
class Feature:
    name: str  # Boss-Extrude1, Cut-Extrude1, ...
    kind: str  # "boss" | "cut"
    sketch: Sketch
    depth_mm: float | None  # None => through-all
    through_all: bool = False


@dataclass
class PartModel:
    """The simulated part: sketches, features, and material footprints.

    ``footprints`` approximates the solid for cut validation: every
    entity of every boss sketch contributes its outline as material on
    that sketch's plane, and every through-all cut records the outline
    it removed (``cut_footprints``). v0.1 approximations, all handled
    with a warning rather than a false error: nested boss contours are
    treated as solid material; cross-plane containment is not checked;
    and the union of several merged same-plane bosses is not computed —
    a cut spanning their seam cannot be proven inside material, so it
    warns instead of failing.
    """

    sketches: list[Sketch] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)
    active_sketch: Sketch | None = None
    saved_to: list[str] = field(default_factory=list)
    _sketch_counter: int = 0
    _boss_counter: int = 0
    _cut_counter: int = 0

    @property
    def has_solid(self) -> bool:
        return any(f.kind == "boss" for f in self.features)

    def footprints(self, plane: PlaneName) -> list[Shape]:
        return [
            shape
            for f in self.features
            if f.kind == "boss" and f.sketch.plane == plane
            for shape in f.sketch.entities
        ]

    def cut_footprints(self, plane: PlaneName) -> list[tuple[str, Shape]]:
        """(feature name, outline) removed by earlier through-all cuts.

        Only through-all cuts count: a blind cut may leave material
        beneath it, so nothing can be concluded about later cuts there.
        """
        return [
            (f.name, shape)
            for f in self.features
            if f.kind == "cut" and f.through_all and f.sketch.plane == plane
            for shape in f.sketch.entities
        ]

    def next_sketch_name(self) -> str:
        self._sketch_counter += 1
        return f"Sketch{self._sketch_counter}"

    def next_feature_name(self, kind: str) -> str:
        if kind == "boss":
            self._boss_counter += 1
            return f"Boss-Extrude{self._boss_counter}"
        self._cut_counter += 1
        return f"Cut-Extrude{self._cut_counter}"

    def summary(self) -> dict[str, object]:
        def shape_dict(s: Shape) -> dict[str, object]:
            if isinstance(s, Rect):
                return {
                    "type": "rectangle",
                    "center": [s.cx, s.cy],
                    "width": s.width,
                    "height": s.height,
                }
            assert isinstance(s, Circle)
            return {"type": "circle", "center": [s.cx, s.cy], "diameter": s.diameter}

        return {
            "units": "mm",
            "sketches": [
                {
                    "name": s.name,
                    "plane": s.plane,
                    "entities": [shape_dict(e) for e in s.entities],
                    "consumed_by": s.consumed_by,
                }
                for s in self.sketches
            ],
            "features": [
                {
                    "name": f.name,
                    "kind": f.kind,
                    "sketch": f.sketch.name,
                    "depth_mm": f.depth_mm,
                    "through_all": f.through_all,
                }
                for f in self.features
            ],
            "saved_to": list(self.saved_to),
        }
