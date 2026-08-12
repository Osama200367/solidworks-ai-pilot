"""Backend contract: one method per primitive command.

Both backends implement identical signatures, so any test that passes
against the mock exercises exactly the operation sequence the COM
backend will receive. All dimensions arrive in millimeters; backends
convert to meters at the COM boundary (see ``calls.py``).

Sketch lifecycle note: there is deliberately no ``close_sketch``
primitive. As in the SolidWorks API itself, ``extrude`` / ``cut_extrude``
consume (and close) the active sketch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swpilot.backends.calls import CallSpec
from swpilot.commands.schema import PlaneName


class BackendError(RuntimeError):
    """A command could not be executed by the backend."""


class Backend(ABC):
    """Executes primitive commands against a model (real or simulated)."""

    name: str = "abstract"

    def __init__(self) -> None:
        self.call_log: list[CallSpec] = []
        self._warnings: list[str] = []

    # -- primitive operations ------------------------------------------

    @abstractmethod
    def new_part(self) -> None: ...

    @abstractmethod
    def create_sketch(self, plane: PlaneName) -> None: ...

    @abstractmethod
    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None: ...

    @abstractmethod
    def draw_circle(self, center: tuple[float, float], diameter: float) -> None: ...

    @abstractmethod
    def extrude(self, depth: float) -> None: ...

    @abstractmethod
    def cut_extrude(self, through_all: bool, depth: float | None) -> None: ...

    @abstractmethod
    def save_part(self, path: str) -> None: ...

    # -- lifecycle / reporting -----------------------------------------

    def finalize(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Called once after all commands succeeded (e.g. zoom-to-fit)."""

    def close(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release any resources (COM references). Default: nothing."""

    @abstractmethod
    def state_summary(self) -> dict[str, object]:
        """A JSON-serializable snapshot of the resulting model."""

    def pop_warnings(self) -> list[str]:
        """Drain warnings accumulated since the last call (per-command)."""
        out, self._warnings = self._warnings, []
        return out

    def _warn(self, message: str) -> None:
        self._warnings.append(message)
