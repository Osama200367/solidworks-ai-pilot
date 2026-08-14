# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Plane frames: sketch-space ↔ world-space mapping.

World coordinates are (x, y, z) in mm. A :class:`PlaneFrame` carries the
2D sketch basis (u, v) and normal of a plane, so sketch points map to
world points and derived faces/edges get real 3D positions for
coordinate-based COM selection.

SolidWorks sketch-axis conventions for the standard planes (sketch +x =
screen-right and sketch +y = screen-up in that plane's normal view):

* Front (normal +Z): u = +X, v = +Y
* Top   (normal +Y): u = +X, v = -Z
* Right (normal +X): u = -Z, v = +Y

These conventions were derived from SolidWorks' standard view
orientations and only affect *world pick coordinates* (sketch entity
coordinates are always passed in sketch space, which is convention-
free). Cross-plane pick coordinates are the first thing to verify in a
Windows smoke test. Offset reference planes inherit their base plane's
frame, which is exactly why SW-Pilot sketches on offset planes instead
of directly on faces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Vec3 = tuple[float, float, float]

PlaneFamily = Literal["front", "top", "right"]
AxisName = Literal["x", "y", "z"]

AXIS_VECTORS: dict[AxisName, Vec3] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

# Which standard plane family has its normal along each world axis.
FAMILY_FOR_AXIS: dict[AxisName, PlaneFamily] = {"x": "right", "y": "top", "z": "front"}
NORMAL_AXIS: dict[PlaneFamily, AxisName] = {"right": "x", "top": "y", "front": "z"}

_FAMILY_BASES: dict[PlaneFamily, tuple[Vec3, Vec3, Vec3]] = {
    # family: (u, v, normal)
    "front": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "right": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
}


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass(frozen=True)
class PlaneFrame:
    """A named planar sketching frame."""

    name: str  # registry name ("front", "SWPilot_Plane1", ...)
    family: PlaneFamily
    offset: float  # signed distance from the standard plane along its normal, mm

    @property
    def u(self) -> Vec3:
        return _FAMILY_BASES[self.family][0]

    @property
    def v(self) -> Vec3:
        return _FAMILY_BASES[self.family][1]

    @property
    def normal(self) -> Vec3:
        return _FAMILY_BASES[self.family][2]

    @property
    def origin(self) -> Vec3:
        return scale(self.normal, self.offset)

    def to_world(self, su: float, sv: float, along_normal: float = 0.0) -> Vec3:
        """Map sketch-space (u, v) plus a normal offset to world mm."""
        p = add(self.origin, add(scale(self.u, su), scale(self.v, sv)))
        return add(p, scale(self.normal, along_normal))


def standard_frame(family: PlaneFamily) -> PlaneFrame:
    return PlaneFrame(name=family, family=family, offset=0.0)


def offset_frame(base: PlaneFrame, name: str, distance: float) -> PlaneFrame:
    """A reference plane parallel to ``base``, ``distance`` mm along its normal."""
    return PlaneFrame(name=name, family=base.family, offset=base.offset + distance)
