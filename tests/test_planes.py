# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Plane frame tests: sketch-space to world-space mapping.

These pin the SolidWorks sketch-axis conventions — if a Windows smoke
test shows a different convention for a standard plane, fixing
``planes.py`` must break exactly these tests.
"""

from swpilot.model.planes import offset_frame, standard_frame


class TestStandardFrames:
    def test_front_maps_identity(self) -> None:
        f = standard_frame("front")
        assert f.to_world(10.0, 5.0) == (10.0, 5.0, 0.0)
        assert f.normal == (0.0, 0.0, 1.0)

    def test_top_sketch_y_is_world_minus_z(self) -> None:
        f = standard_frame("top")
        assert f.to_world(10.0, 5.0) == (10.0, 0.0, -5.0)
        assert f.normal == (0.0, 1.0, 0.0)

    def test_right_sketch_x_is_world_minus_z(self) -> None:
        f = standard_frame("right")
        assert f.to_world(10.0, 5.0) == (0.0, 5.0, -10.0)
        assert f.normal == (1.0, 0.0, 0.0)

    def test_along_normal_offset(self) -> None:
        f = standard_frame("front")
        assert f.to_world(1.0, 2.0, 3.0) == (1.0, 2.0, 3.0)


class TestOffsetFrames:
    def test_offset_moves_along_normal(self) -> None:
        base = standard_frame("front")
        off = offset_frame(base, "P1", 12.0)
        assert off.offset == 12.0
        assert off.to_world(10.0, 5.0) == (10.0, 5.0, 12.0)
        assert off.family == "front"

    def test_negative_offset(self) -> None:
        off = offset_frame(standard_frame("top"), "P2", -7.0)
        assert off.to_world(0.0, 0.0) == (0.0, -7.0, 0.0)

    def test_stacked_offsets_accumulate(self) -> None:
        p1 = offset_frame(standard_frame("right"), "P1", 10.0)
        p2 = offset_frame(p1, "P2", 5.0)
        assert p2.offset == 15.0
        assert p2.to_world(0.0, 0.0) == (15.0, 0.0, 0.0)
