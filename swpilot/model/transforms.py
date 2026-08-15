# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Axis-aligned rigid transforms for assembly components.

v0.3 restricts component orientation to 90-degree rotation steps about
world axes, so rotation matrices contain only -1/0/1 entries and every
transformed box/axis stays axis-aligned — the twin's selector and mate
math remains exact.
"""

from __future__ import annotations

from dataclasses import dataclass

from swpilot.model.planes import AxisName, Vec3

Mat3 = tuple[Vec3, Vec3, Vec3]  # rows

IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _matvec(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _matmul(a: Mat3, b: Mat3) -> Mat3:
    rows = []
    for i in range(3):
        row = tuple(
            a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] for j in range(3)
        )
        rows.append(row)
    return (rows[0], rows[1], rows[2])  # type: ignore[return-value]


def _rot90(axis: AxisName, quarter_turns: int) -> Mat3:
    """Rotation about a world axis by quarter_turns * 90 degrees."""
    q = quarter_turns % 4
    c = [1.0, 0.0, -1.0, 0.0][q]
    s = [0.0, 1.0, 0.0, -1.0][q]
    if axis == "x":
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == "y":
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


@dataclass(frozen=True)
class Transform:
    """world = R @ local + t  (t in mm)."""

    rotation: Mat3 = IDENTITY
    translation: Vec3 = (0.0, 0.0, 0.0)

    def apply(self, p: Vec3) -> Vec3:
        r = _matvec(self.rotation, p)
        return (
            r[0] + self.translation[0],
            r[1] + self.translation[1],
            r[2] + self.translation[2],
        )

    def rotate(self, v: Vec3) -> Vec3:
        """Rotate a direction (no translation)."""
        return _matvec(self.rotation, v)

    def rotate_back(self, v: Vec3) -> Vec3:
        """Inverse-rotate a direction (R is orthonormal: inverse = transpose)."""
        m = self.rotation
        return (
            m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
            m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
            m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
        )

    def with_translation(self, t: Vec3) -> Transform:
        return Transform(rotation=self.rotation, translation=t)

    def to_row_major(self) -> list[float]:
        """Rotation as 9 row-major floats (for reports / COM math transforms)."""
        return [self.rotation[i][j] for i in range(3) for j in range(3)]


@dataclass
class RotationStep:
    axis: AxisName
    degrees: int  # multiple of 90, non-zero

    def __post_init__(self) -> None:
        if self.degrees % 90 != 0 or self.degrees % 360 == 0:
            raise ValueError(
                f"rotation degrees must be a non-zero multiple of 90, got {self.degrees}"
            )


def build_transform(
    steps: list[RotationStep] | None, translation: Vec3, _acc: Mat3 = IDENTITY
) -> Transform:
    """Compose rotation steps (applied in order, about world axes)."""
    r = _acc
    for step in steps or []:
        r = _matmul(_rot90(step.axis, step.degrees // 90), r)
    # Snap float noise: entries are exactly -1/0/1 by construction.
    snapped = tuple(
        tuple(round(r[i][j]) * 1.0 for j in range(3)) for i in range(3)
    )
    return Transform(rotation=snapped, translation=translation)  # type: ignore[arg-type]
