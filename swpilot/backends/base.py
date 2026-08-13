"""Backend contract: one method per primitive command.

Both backends implement identical signatures against pre-resolved
inputs: the executor's :class:`~swpilot.model.tracker.ModelTracker`
validates every command and resolves selectors to concrete data (plane
display names, feature names, edge pick coordinates) before any backend
method runs. Backends therefore do no validation of their own — the mock
logs the shared call plan, the COM backend executes the same plan.

All dimensions arrive in millimeters and degrees; the shared call
builders convert to meters/radians at the COM boundary.

Sketch lifecycle note: there is deliberately no ``close_sketch``
primitive. As in the SolidWorks API itself, ``extrude`` / ``cut_extrude``
consume (and close) the active sketch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swpilot.backends.calls import CallSpec

Vec3 = tuple[float, float, float]


class BackendError(RuntimeError):
    """A command could not be executed by the backend."""


class Backend(ABC):
    """Executes primitive commands against a model (real or logged)."""

    name: str = "abstract"

    def __init__(self) -> None:
        self.call_log: list[CallSpec] = []
        self._warnings: list[str] = []
        self._active_doc: str | None = None

    # -- document lifecycle --------------------------------------------

    def mark_active(self, name: str) -> None:
        """Record that a just-created document is the active one."""
        self._active_doc = name

    def ensure_active(self, name: str, kind: str) -> None:
        """Switch documents only when needed (no-op when already active)."""
        if self._active_doc != name:
            self.activate_document(name, kind)
            self._active_doc = name

    @abstractmethod
    def activate_document(self, name: str, kind: str) -> None: ...

    # -- primitive operations ------------------------------------------

    @abstractmethod
    def new_part(self, name: str) -> None: ...

    @abstractmethod
    def new_assembly(self, name: str) -> None: ...

    @abstractmethod
    def insert_component(
        self,
        path: str,
        name: str,
        translation: tuple[float, float, float],
        rotation_row_major: list[float] | None,
        fixed: bool,
        external: bool,
    ) -> None: ...

    @abstractmethod
    def add_mate(
        self,
        mate_type: str,
        pick_a: Vec3,
        pick_b: Vec3,
        value: float | None,
        name: str,
    ) -> None: ...

    @abstractmethod
    def save_assembly(self, path: str) -> None: ...

    @abstractmethod
    def create_plane(self, name: str, base_display: str, distance: float) -> None: ...

    @abstractmethod
    def create_axis(self, axis: str, name: str) -> None: ...

    @abstractmethod
    def create_sketch(self, plane_display: str) -> None: ...

    @abstractmethod
    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None: ...

    @abstractmethod
    def draw_circle(self, center: tuple[float, float], diameter: float) -> None: ...

    @abstractmethod
    def draw_slot(
        self, start: tuple[float, float], end: tuple[float, float], width: float
    ) -> None: ...

    @abstractmethod
    def extrude(self, depth: float, reverse: bool, name: str) -> None: ...

    @abstractmethod
    def cut_extrude(
        self,
        through_all: bool,
        depth: float | None,
        reverse: bool,
        draft_angle: float | None,
        name: str,
    ) -> None: ...

    @abstractmethod
    def fillet(self, edge_points: list[Vec3], radius: float, name: str) -> None: ...

    @abstractmethod
    def chamfer(
        self, edge_points: list[Vec3], distance: float, angle: float, name: str
    ) -> None: ...

    @abstractmethod
    def linear_pattern(
        self,
        feature_names: list[str],
        axis_feature1: str,
        flip1: bool,
        spacing1: float,
        count1: int,
        dir2: tuple[str, bool, float, int] | None,
        name: str,
    ) -> None: ...

    @abstractmethod
    def circular_pattern(
        self,
        feature_names: list[str],
        axis_feature: str,
        count: int,
        total_angle: float,
        equal_spacing: bool,
        name: str,
    ) -> None: ...

    @abstractmethod
    def save_part(self, path: str) -> None: ...

    # -- lifecycle / reporting -----------------------------------------

    def finalize(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Called once after all commands succeeded (e.g. zoom-to-fit)."""

    def close(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release any resources (COM references). Default: nothing."""

    @abstractmethod
    def state_summary(self) -> dict[str, object]:
        """Backend-side model info (COM: read-back feature tree)."""

    def pop_warnings(self) -> list[str]:
        """Drain warnings accumulated since the last call (per-command)."""
        out, self._warnings = self._warnings, []
        return out

    def _warn(self, message: str) -> None:
        self._warnings.append(message)
