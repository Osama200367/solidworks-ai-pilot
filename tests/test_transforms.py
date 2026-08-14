# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Axis-aligned transform tests."""

import pytest

from swpilot.model.transforms import RotationStep, Transform, build_transform


class TestRotations:
    def test_identity(self) -> None:
        t = build_transform([], (1.0, 2.0, 3.0))
        assert t.apply((10.0, 0.0, 0.0)) == (11.0, 2.0, 3.0)

    def test_z90_maps_x_to_y(self) -> None:
        t = build_transform([RotationStep("z", 90)], (0.0, 0.0, 0.0))
        assert t.apply((1.0, 0.0, 0.0)) == (0.0, 1.0, 0.0)
        assert t.apply((0.0, 1.0, 0.0)) == (-1.0, 0.0, 0.0)

    def test_x180_flips_yz(self) -> None:
        t = build_transform([RotationStep("x", 180)], (0.0, 0.0, 0.0))
        assert t.apply((0.0, 1.0, 1.0)) == (0.0, -1.0, -1.0)

    def test_negative_quarter(self) -> None:
        t = build_transform([RotationStep("z", -90)], (0.0, 0.0, 0.0))
        assert t.apply((1.0, 0.0, 0.0)) == (0.0, -1.0, 0.0)

    def test_composition_order(self) -> None:
        # rotate about x then z, both world axes: z(90) @ x(90)
        t = build_transform(
            [RotationStep("x", 90), RotationStep("z", 90)], (0.0, 0.0, 0.0)
        )
        # x90: y->z; then z90 leaves z alone
        assert t.apply((0.0, 1.0, 0.0)) == (0.0, 0.0, 1.0)
        # x90: z->-y; z90: -y->x
        assert t.apply((0.0, 0.0, 1.0)) == (1.0, 0.0, 0.0)

    def test_rotate_back_inverts(self) -> None:
        t = build_transform([RotationStep("y", 90)], (0.0, 0.0, 0.0))
        v = (0.0, 0.0, -1.0)
        assert t.rotate_back(t.rotate(v)) == v

    def test_entries_are_exact_integers(self) -> None:
        t = build_transform([RotationStep("x", 90), RotationStep("y", -90)], (0, 0, 0))
        assert all(v in (-1.0, 0.0, 1.0) for row in t.rotation for v in row)

    def test_invalid_degrees_rejected(self) -> None:
        with pytest.raises(ValueError, match="multiple of 90"):
            RotationStep("x", 45)
        with pytest.raises(ValueError, match="multiple of 90"):
            RotationStep("x", 360)

    def test_row_major_layout(self) -> None:
        t = Transform()
        assert t.to_row_major() == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
