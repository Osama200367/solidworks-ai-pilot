# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Mock backend: logs the exact COM call plan, executes nothing.

All validation and state tracking lives in the shared
:class:`~swpilot.model.tracker.ModelTracker`, which the executor runs
for every backend — so this class is deliberately thin: each method
appends the shared :class:`CallSpec` sequence the COM backend would
execute, and that is all.
"""

from __future__ import annotations

from swpilot.backends import calls
from swpilot.backends.base import Backend, Vec3
from swpilot.model.drawing import DimSpec, DrawingSetup, NoteSpec, SectionSpec, ViewSpec


class MockBackend(Backend):
    name = "mock"

    def new_part(self, name: str) -> None:
        self.call_log.extend(calls.new_part_calls())

    def new_assembly(self, name: str) -> None:
        self.call_log.extend(calls.new_assembly_calls())

    def activate_document(self, name: str, kind: str) -> None:
        self.call_log.extend(calls.activate_document_calls(name))

    def insert_component(
        self,
        path: str,
        name: str,
        translation: tuple[float, float, float],
        rotation_row_major: list[float] | None,
        fixed: bool,
        external: bool,
    ) -> None:
        if external:
            self.call_log.extend(calls.open_external_part_calls(path))
            self.call_log.extend(
                calls.activate_document_calls(self._active_doc or "<asm>")
            )
        self.call_log.extend(calls.insert_component_calls(path, name, translation))
        if rotation_row_major is not None:
            self.call_log.extend(
                calls.component_transform_calls(name, rotation_row_major, translation)
            )
        if fixed:
            self.call_log.extend(calls.fix_component_calls(name))

    def add_mate(
        self,
        mate_type: str,
        pick_a: Vec3,
        pick_b: Vec3,
        value: float | None,
        name: str,
    ) -> None:
        self.call_log.extend(calls.add_mate_calls(mate_type, pick_a, pick_b, value, name))

    def save_assembly(self, path: str) -> None:
        self.call_log.extend(calls.save_assembly_calls(path))

    def create_plane(self, name: str, base_display: str, distance: float) -> None:
        self.call_log.extend(calls.create_plane_calls(name, base_display, distance))

    def create_axis(self, axis: str, name: str) -> None:
        self.call_log.extend(calls.create_axis_calls(axis, name))

    def create_sketch(self, plane_display: str) -> None:
        self.call_log.extend(calls.create_sketch_calls(plane_display))

    def draw_rectangle(self, center: tuple[float, float], width: float, height: float) -> None:
        self.call_log.extend(calls.draw_rectangle_calls(center, width, height))

    def draw_circle(self, center: tuple[float, float], diameter: float) -> None:
        self.call_log.extend(calls.draw_circle_calls(center, diameter))

    def draw_slot(
        self, start: tuple[float, float], end: tuple[float, float], width: float
    ) -> None:
        self.call_log.extend(calls.draw_slot_calls(start, end, width))

    def extrude(self, depth: float, reverse: bool, name: str) -> None:
        self.call_log.extend(calls.extrude_calls(depth, reverse, name))

    def cut_extrude(
        self,
        through_all: bool,
        depth: float | None,
        reverse: bool,
        draft_angle: float | None,
        name: str,
    ) -> None:
        self.call_log.extend(
            calls.cut_extrude_calls(through_all, depth, reverse, draft_angle, name)
        )

    def fillet(self, edge_points: list[Vec3], radius: float, name: str) -> None:
        self.call_log.extend(calls.fillet_calls(edge_points, radius, name))

    def chamfer(
        self, edge_points: list[Vec3], distance: float, angle: float, name: str
    ) -> None:
        self.call_log.extend(calls.chamfer_calls(edge_points, distance, angle, name))

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
        self.call_log.extend(
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
        self.call_log.extend(
            calls.circular_pattern_calls(
                feature_names, axis_feature, count, total_angle, equal_spacing, name
            )
        )

    def save_part(self, path: str) -> None:
        self.call_log.extend(calls.save_part_calls(path))

    def draw_spline(self, points: list[tuple[float, float]]) -> None:
        self.call_log.extend(calls.draw_spline_calls(points))

    def draw_arc(
        self,
        center: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
        ccw: bool,
    ) -> None:
        self.call_log.extend(calls.draw_arc_calls(center, start, end, ccw))

    def draw_line(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        self.call_log.extend(calls.draw_line_calls(start, end))

    def revolve(self, axis_feature: str, angle: float, reverse: bool, name: str) -> None:
        self.call_log.extend(calls.revolve_calls(axis_feature, angle, reverse, name))

    def helix_thread(
        self,
        diameter: float,
        pitch: float,
        length: float,
        right_handed: bool,
        revolutions: float,
        name: str,
    ) -> None:
        self.call_log.extend(
            calls.helix_thread_calls(diameter, pitch, length, right_handed, revolutions, name)
        )

    def create_drawing(self, setup: DrawingSetup) -> None:
        # The title-block properties live on the model document; switch to
        # it first if the drawing flow left another document active.
        if self._active_doc != setup.model_doc:
            self.call_log.extend(calls.activate_document_calls(setup.model_doc))
            self._active_doc = setup.model_doc
        self.call_log.extend(calls.custom_property_calls(setup.properties))
        self.call_log.extend(calls.new_drawing_calls(setup.sheet))
        self.call_log.extend(
            calls.setup_sheet_calls(
                setup.sheet,
                setup.scale[0],
                setup.scale[1],
                setup.first_angle,
                setup.paper_w,
                setup.paper_h,
            )
        )
        self.call_log.extend(
            calls.note_calls(setup.units_note_text, setup.units_note_position)
        )

    def add_views(self, views: list[ViewSpec]) -> None:
        for v in views:
            if v.method == "model":
                assert v.model_path is not None and v.orientation is not None
                self.call_log.extend(
                    calls.model_view_calls(
                        v.model_path, v.orientation, v.position, v.name, v.scale
                    )
                )
            else:
                assert v.parent is not None
                self.call_log.extend(
                    calls.projected_view_calls(v.parent, v.position, v.name)
                )

    def add_section_view(self, spec: SectionSpec) -> None:
        self.call_log.extend(
            calls.section_view_calls(
                spec.parent, spec.line, spec.label, spec.position, spec.name
            )
        )

    def add_annotations(self, dims: list[DimSpec], notes: list[NoteSpec]) -> None:
        for d in dims:
            self.call_log.extend(
                calls.dimension_calls(
                    d.picks,
                    d.placement,
                    d.prefix,
                    d.below,
                    f"{d.kind} dimension '{d.name}' = {d.value:g} mm in view '{d.view}'",
                )
            )
        for n in notes:
            self.call_log.extend(calls.note_calls(n.text, n.position))

    def save_drawing(self, path: str) -> None:
        self.call_log.extend(calls.save_drawing_calls(path))

    def finalize(self) -> None:
        self.call_log.extend(calls.finalize_calls())

    def state_summary(self) -> dict[str, object]:
        return {"backend": "mock", "calls": len(self.call_log)}
