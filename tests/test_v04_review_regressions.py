# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning fixes from the v0.4 adversarial review.

14 findings were confirmed (0 refuted); several were duplicates of the
same defect, leaving 10 distinct fixes. Each test class pins one. The
COM-only fixes (live sheet name, CustomPropertyManager resolver root)
are covered by the WINDOWS_SETUP checklist plus the call-plan assertions
here.
"""

import pytest

from swpilot.backends import calls
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand, parse_command_data
from swpilot.executor import execute
from swpilot.model.dimensioning import analyze
from swpilot.model.drawing import DrawingTracker
from swpilot.model.tracker import ModelTracker
from tests.test_dimensioning import drawing_for, make_flange
from tests.test_e2e_drawing import EXAMPLES


class TestSectionLineInViewCoordinates:
    """Finding 1 (critical): view-activated sketching is view-space, not
    sheet-space — the cutting line must be model-scale mm about the view
    origin."""

    def test_flange_cutting_line_is_centered_on_the_model(self) -> None:
        _, expanded = load_and_expand(EXAMPLES / "flange_drawing.json")
        backend = MockBackend()
        report = execute(expanded, backend)
        assert report.success
        line = next(c for c in backend.call_log if c.method == "CreateLine")
        x1, y1, _, x2, y2, _ = line.args
        # vertical line through the flange axis: x = 0 in view space
        # (the model origin IS the axis), spanning past the Ø120 disc
        assert x1 == x2 == pytest.approx(0.0)
        assert y1 == pytest.approx(-0.068) and y2 == pytest.approx(0.068)
        # while the section PLACEMENT stays in absolute sheet space
        sec = next(c for c in backend.call_log if c.method == "CreateSectionViewAt5")
        assert sec.args[0] > 0.05  # sheet-range meters, not view-relative

    def test_off_origin_part_line_lands_on_the_part(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((50, 30), 40, 20)  # model center far from origin
        part.extrude(10, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front"], scale=(1, 1))
        spec = d.section_view("front", "vertical")
        # In view space the part spans x in [30, 70]; the center line must
        # pass through x = 50 (the projected AABB center), not through 0.
        assert spec.line[0] == spec.line[2] == pytest.approx(50.0)


class TestProjectionInvariantImages:
    """Finding 2 (critical): first vs third angle flips placement only —
    an individual view's image is identical under both conventions."""

    def test_first_angle_right_view_pick_not_mirrored(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 100, 60)
        part.extrude(20, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d3 = drawing_for(part, ["front", "top", "right"])
        d1 = DrawingTracker(
            name="d1", model_doc="p", model=part, model_path="p.SLDPRT",
            sheet="A3", scale=(1, 2), projection="first",
            title=None, drawn_by="x", date="",
        )
        d1.standard_views(["front", "top", "right"])
        p = (10.0, 5.0, 3.0)
        assert d3.project(d3.views["right"], p) == d1.project(d1.views["right"], p)
        assert d3.project(d3.views["top"], p) == d1.project(d1.views["top"], p)


class TestCbCskPairingFamilyCheck:
    """Finding 3 (critical): cb/csk pairing across plane families would
    fabricate callouts for counterbores that do not exist."""

    def test_cross_family_holes_not_paired(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 100, 100)
        part.extrude(50, False)
        # through hole on the front family at sketch (0, 0)
        part.create_sketch("front")
        part.draw_circle((0, 0), 8)
        part.cut_extrude(True, None, False, None)
        # UNRELATED blind pocket on the top family, also sketch (0, 0),
        # larger diameter — numerically center-identical, different world
        part.create_plane("top_off", "top", 50)
        part.create_sketch("top_off")
        part.draw_circle((0, 0), 20)
        part.cut_extrude(False, 5, True, None)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        callouts = {c.name: c for c in dims if c.name.startswith("callout_")}
        # the through hole keeps a plain callout (no fabricated CBORE line)
        assert callouts["callout_Cut-Extrude1"].below is None
        # and the pocket gets its own callout instead of being consumed
        assert "callout_Cut-Extrude2" in callouts


class TestBoreSkipByFeatureName:
    """Finding 9 (major): the bore skip matched sketch coordinates without
    the plane family, silently dropping cross-drilled holes."""

    def test_cross_drilled_hole_gets_a_callout(self) -> None:
        part = make_flange()  # front-family turned part, bore Cut-Extrude1
        # radial cross-hole on the top family at sketch (0, 0): shares the
        # turned center NUMERICALLY but is a different world position
        part.create_sketch("top")
        part.draw_circle((0, 0), 6)
        part.cut_extrude(True, None, False, None)
        part.save_part("p2.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        names = {c.name for c in dims}
        assert "callout_Cut-Extrude3" in names  # the cross-hole
        assert "callout_Cut-Extrude1" not in names  # the real bore stays out


class TestNoteStacking:
    """Findings 6/10/11 (major): successive note blocks restarted at the
    same y and printed on top of each other."""

    def test_bracket_notes_have_distinct_positions(self) -> None:
        _, expanded = load_and_expand(EXAMPLES / "bracket_drawing.json")
        backend = MockBackend()
        report = execute(expanded, backend)
        assert report.success
        positions = [
            c.args[:2]
            for c in backend.call_log
            if c.method == "SetPosition2" and c.target == "LastFeatureAnnotation"
        ]
        assert len(positions) == len(set(positions))  # no two notes collide
        # the B.C. note and the feature block stack downward 6mm apart
        note_ys = sorted({p[1] for p in positions[1:]}, reverse=True)  # skip units note
        steps = [round((a - b) * 1000, 3) for a, b in zip(note_ys, note_ys[1:], strict=False)]
        assert steps == [6.0, 6.0, 6.0]


class TestSectionUsability:
    """Finding 7 (major): a section that collapses the turned axis must
    not carry length/step dims (degenerate identical picks)."""

    def make_right_family_shaft(self) -> ModelTracker:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("right")
        part.draw_circle((0, 0), 40)
        part.extrude(60, False)
        part.create_sketch("right")
        part.draw_circle((0, 0), 16)
        part.cut_extrude(True, None, False, None)
        part.save_part("shaft.SLDPRT")
        part.pop_warnings()
        return part

    def test_vertical_section_of_right_family_part_falls_back(self) -> None:
        # right-family axis is world x; a vertical section of the front
        # view has image (-z, y) — the axis is collapsed.
        d = drawing_for(self.make_right_family_shaft(), ["front", "top", "right"], section=True)
        dims, _, warnings = analyze(d)
        by_name = {c.name: c for c in dims}
        assert by_name["length"].view != "section_A"
        assert any("collapses the turned axis" in w for w in warnings)

    def test_horizontal_section_carries_axis_dims_with_u_picks(self) -> None:
        part = self.make_right_family_shaft()
        d = drawing_for(part, ["front"])
        d.section_view("front", "horizontal")
        d.pop_warnings()
        dims, _, _ = analyze(d)
        by_name = {c.name: c for c in dims}
        length = by_name["length"]
        assert length.view == "section_A"
        assert length.value == 60.0
        # non-degenerate: the two picks differ along the axis direction
        assert length.picks[0] != length.picks[1]
        bore = by_name["bore"]
        assert bore.view == "section_A"
        assert bore.picks[0] != bore.picks[1]


class TestThicknessSpansMergedMaterial:
    """Finding 8 (major): the thickness span dropped the base sketch
    offset and later bosses."""

    def test_offset_base_plane(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_plane("up", "front", 5)
        part.create_sketch("up")
        part.draw_rectangle((0, 0), 80, 60)
        part.extrude(10, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        t = next(c for c in dims if c.name == "thickness")
        assert t.value == 10.0
        # picks at the real cap planes z=5 and z=15 (right view: u = -z)
        right = d.views["right"]
        xs = sorted(p[0] for p in t.picks)
        assert xs[0] == pytest.approx(right.center[0] + (-15 + 10) * right.scale)
        assert xs[1] == pytest.approx(right.center[0] + (-5 + 10) * right.scale)

    def test_stacked_boss_extends_the_value(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 80, 60)
        part.extrude(10, False)
        part.create_plane("top_face", "front", 10)
        part.create_sketch("top_face")
        part.draw_rectangle((0, 0), 80, 60)
        part.extrude(8, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        t = next(c for c in dims if c.name == "thickness")
        assert t.value == 18.0  # merged extent, not the base's 10

    def test_gap_between_bosses_warns(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 80, 60)
        part.extrude(10, False)
        part.create_plane("floating", "front", 30)
        part.create_sketch("floating")
        part.draw_rectangle((0, 0), 80, 60)
        part.extrude(5, False)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        _, _, warnings = analyze(d)
        assert any("spans a gap" in w for w in warnings)


class TestActivateSheetHardened:
    """Findings 4/12 (major): ActivateSheet must fail loudly and accept a
    live sheet name (custom templates do not use 'Sheet1')."""

    def test_check_is_truthy_and_name_parameterized(self) -> None:
        specs = calls.section_view_calls(
            "front", (0, -10, 0, 10), "A", (100, 50), "section_A", sheet_name="Blatt1"
        )
        act = next(c for c in specs if c.method == "ActivateSheet")
        assert act.args == ("Blatt1",)
        assert act.check == "truthy"

    def test_default_keeps_mock_parity(self) -> None:
        specs = calls.section_view_calls("front", (0, -10, 0, 10), "A", (100, 50), "s")
        act = next(c for c in specs if c.method == "ActivateSheet")
        assert act.args == (calls.SW_SHEET1,)


class TestCustomPropertiesOverwrite:
    """Finding 5 (major): AddCustomInfo3 never overwrites, so a second
    drawing of the same model kept stale title-block values."""

    def test_add3_delete_and_add(self) -> None:
        specs = calls.custom_property_calls([("Description", "X")])
        (spec,) = specs
        assert spec.target == "CustomPropertyManager"
        assert spec.method == "Add3"
        assert spec.args == ("Description", calls.SW_CUSTOM_INFO_TEXT, "X", 1)
        assert spec.check == "status_zero"  # swCustomInfoAddResult 0 = ok

    def test_second_drawing_of_same_model_updates_properties(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.4",
                "commands": [
                    {"op": "create_plate", "width": 60, "height": 40, "thickness": 5},
                    {"op": "save_part", "path": "p.SLDPRT"},
                    {"op": "create_drawing", "name": "d1", "of": "Part1",
                     "sheet": "A4", "title": "FIRST"},
                    {"op": "activate_document", "name": "Part1"},
                    {"op": "create_drawing", "name": "d2", "of": "Part1",
                     "sheet": "A4", "title": "SECOND"},
                ],
            }
        )
        from swpilot.commands.loader import expand_commands

        backend = MockBackend()
        report = execute(expand_commands(list(cf.commands)), backend)
        assert report.success
        descriptions = [
            c.args[2] for c in backend.call_log
            if c.method == "Add3" and c.args[0] == "Description"
        ]
        assert descriptions == ["FIRST", "SECOND"]  # both actually applied


class TestExternalPreloadParity:
    """Finding 13 (minor): both backends must gate the OpenDoc6 preload on
    the same schema-level predicate, even for a same-run-saved path."""

    def test_mock_preloads_file_insert_of_same_run_saved_path(self) -> None:
        cf = parse_command_data(
            {
                "schema_version": "0.4",
                "commands": [
                    {"op": "create_plate", "width": 60, "height": 40, "thickness": 5},
                    {"op": "save_part", "path": "pad.SLDPRT"},
                    {"op": "new_assembly", "name": "asm"},
                    {"op": "insert_component", "file": "pad.SLDPRT",
                     "name": "pad_1", "envelope": [60, 40, 5]},
                ],
            }
        )
        from swpilot.commands.loader import expand_commands

        backend = MockBackend()
        report = execute(expand_commands(list(cf.commands)), backend)
        assert report.success
        methods = [c.method for c in backend.call_log]
        # file: inserts ALWAYS preload — the COM backend now uses the same
        # schema-level gate (OpenDoc6 on an open file is harmless)
        assert "OpenDoc6" in methods
        assert methods.index("OpenDoc6") < methods.index("AddComponent5")


class TestAssemblyDocumentSpec:
    """Finding 14 (minor): the COM backend logged the part-document spec
    for assemblies; the shared builder now exists for both."""

    def test_new_assembly_document_note(self) -> None:
        spec = calls.new_assembly_document("t.asmdot")
        assert "assembly" in spec.note
        assert calls.new_assembly_calls("t.asmdot")[1] == spec
