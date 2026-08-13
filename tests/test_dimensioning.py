"""Smart-dimension analyzer tests: exact governing sets, never a dump."""

import pytest

from swpilot.model.dimensioning import analyze
from swpilot.model.drawing import DrawingTracker
from swpilot.model.tracker import ModelTracker


def make_flange() -> ModelTracker:
    part = ModelTracker()
    part.new_part()
    part.create_sketch("front")
    part.draw_circle((0, 0), 120)
    part.extrude(15, False)
    part.create_plane("hub_plane", "front", 15)
    part.create_sketch("hub_plane")
    part.draw_circle((0, 0), 60)
    part.extrude(25, False)
    part.create_sketch("front")
    part.draw_circle((0, 0), 30)
    part.cut_extrude(True, None, False, None)
    part.create_sketch("front")
    part.draw_circle((45, 0), 9)
    part.cut_extrude(True, None, False, None)
    part.create_axis("z")
    part.circular_pattern(["Cut-Extrude2"], "z", 6, 360.0, True)
    part.save_part("flange.SLDPRT")
    part.pop_warnings()
    return part


def make_bracket() -> ModelTracker:
    part = ModelTracker()
    part.new_part()
    part.create_sketch("front")
    part.draw_rectangle((0, 0), 120, 80)
    part.extrude(12, False)
    part.fillet(8, "vertical_corners", None, None)
    part.create_sketch("front")
    part.draw_circle((-45, -25), 6.6)
    part.draw_circle((45, -25), 6.6)
    part.cut_extrude(True, None, False, None)
    part.save_part("bracket.SLDPRT")
    part.pop_warnings()
    return part


def drawing_for(
    part: ModelTracker,
    views: list[str],
    section: bool = False,
    scale: tuple[int, int] = (1, 2),
) -> DrawingTracker:
    d = DrawingTracker(
        name="d",
        model_doc="p",
        model=part,
        model_path="p.SLDPRT",
        sheet="A3",
        scale=scale,
        projection="third",
        title=None,
        drawn_by="x",
        date="",
    )
    d.standard_views(views)
    if section:
        d.section_view("front", "vertical")
    d.pop_warnings()
    return d


class TestRectangularEnvelope:
    def test_w_h_t_emitted_with_correct_values(self) -> None:
        d = drawing_for(make_bracket(), ["front", "top", "right"])
        dims, _, _ = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        assert by_name["envelope_width"].value == 120.0
        assert by_name["envelope_height"].value == 80.0
        assert by_name["thickness"].value == 12.0
        assert by_name["envelope_width"].view == "front"
        assert by_name["thickness"].view == "right"

    def test_width_uses_two_opposite_outline_picks(self) -> None:
        # A single bottom edge would measure W - 2*fillet_r; the picks must
        # be on the left and right outline lines instead.
        d = drawing_for(make_bracket(), ["front", "top", "right"])
        dims, _, _ = analyze(d)
        w = next(dim for dim in dims if dim.name == "envelope_width")
        assert len(w.picks) == 2
        front = d.views["front"]
        assert w.picks[0][0] == pytest.approx(front.center[0] - 60 * front.scale)
        assert w.picks[1][0] == pytest.approx(front.center[0] + 60 * front.scale)
        # both picks at mid-height, where the lateral faces exist despite fillets
        assert w.picks[0][1] == pytest.approx(front.center[1])

    def test_thickness_view_fallback_and_warning(self) -> None:
        d = drawing_for(make_bracket(), ["front"])
        dims, _, warnings = analyze(d)
        assert not any(dim.name == "thickness" for dim in dims)
        assert any("thickness skipped" in w for w in warnings)


class TestHoleCallouts:
    def test_counterbore_pairing(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 100, 60)
        part.extrude(12, False)
        # through holes + concentric counterbores (as the hole macro emits)
        part.create_sketch("front")
        part.draw_circle((-30, 0), 6.6)
        part.draw_circle((30, 0), 6.6)
        part.cut_extrude(True, None, False, None)
        part.create_plane("cb_plane", "front", 12)
        part.create_sketch("cb_plane")
        part.draw_circle((-30, 0), 11)
        part.draw_circle((30, 0), 11)
        part.cut_extrude(False, 6, True, None)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        callouts = [dim for dim in dims if dim.name.startswith("callout_")]
        assert len(callouts) == 1  # the cb cut is folded into the through callout
        c = callouts[0]
        assert c.value == 6.6
        assert c.prefix == "2X "
        assert c.below == "<HOLE-SPOT><MOD-DIAM>11 <HOLE-DEPTH>6"

    def test_countersink_pairing_reports_major_diameter(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 100, 60)
        part.extrude(12, False)
        # csk cone exactly as the hole macro emits it: sketched at the MAJOR
        # (surface) diameter, drafted inward, then the through hole
        part.create_plane("cs_plane", "front", 12)
        part.create_sketch("cs_plane")
        part.draw_circle((0, 0), 12.6)
        part.cut_extrude(False, 3.0, True, 45.0)
        part.create_sketch("cs_plane")
        part.draw_circle((0, 0), 6.6)
        part.cut_extrude(True, None, True, None)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        c = next(dim for dim in dims if dim.name.startswith("callout_"))
        assert c.value == 6.6
        # surface diameter 12.6; included angle = 2 x 45 draft
        assert c.below == "<HOLE-SINK><MOD-DIAM>12.6 X 90<MOD-DEG>"

    def test_position_dims_from_datum_edges(self) -> None:
        d = drawing_for(make_bracket(), ["front", "top", "right"])
        dims, _, _ = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        # datum hole (-45, -25); datum edges at x=-60, y=-40
        assert by_name["pos_x_Cut-Extrude1"].value == 15.0
        assert by_name["pos_y_Cut-Extrude1"].value == 15.0

    def test_missing_true_view_warns(self) -> None:
        part = make_bracket()
        d = DrawingTracker(
            name="d", model_doc="p", model=part, model_path="p.SLDPRT",
            sheet="A3", scale=(1, 2), projection="third",
            title=None, drawn_by="x", date="",
        )
        d.isometric_view("top_right", None)
        dims, _, warnings = analyze(d)
        assert not any(dim.name.startswith("callout_") for dim in dims)
        assert any("hole callout" in w and "skipped" in w for w in warnings)


class TestPatterns:
    def test_linear_pattern_pitch_dim(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 120, 80)
        part.extrude(12, False)
        part.create_sketch("front")
        part.draw_circle((-40, -20), 8)
        part.cut_extrude(True, None, False, None)
        part.create_axis("x")
        part.linear_pattern(["Cut-Extrude1"], "x", 40.0, 3, None)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        dims, _, _ = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        assert by_name["callout_Cut-Extrude1"].prefix == "3X "
        pitch = by_name["pitch_LPattern1"]
        assert pitch.value == 40.0
        assert len(pitch.picks) == 2
        # picks one pattern step apart in sheet x
        front = d.views["front"]
        assert pitch.picks[1][0] - pitch.picks[0][0] == pytest.approx(40 * front.scale)

    def test_circular_pattern_gets_bc_note_not_position_dims(self) -> None:
        d = drawing_for(make_flange(), ["front"], section=True)
        dims, notes, _ = analyze(d)
        assert not any(dim.name.startswith("pos_") for dim in dims)
        callout = next(dim for dim in dims if dim.name.startswith("callout_"))
        assert callout.prefix == "6X "
        assert any(
            n.text == "<MOD-DIAM>9 HOLES EQUALLY SPACED ON <MOD-DIAM>90 B.C."
            for n in notes
        )


class TestTurnedParts:
    def test_outer_diameters_on_true_view(self) -> None:
        d = drawing_for(make_flange(), ["front"], section=True)
        dims, _, _ = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        assert by_name["od_1"].value == 120.0 and by_name["od_1"].kind == "diameter"
        assert by_name["od_2"].value == 60.0
        assert by_name["od_1"].view == "front"

    def test_section_carries_length_step_and_bore(self) -> None:
        d = drawing_for(make_flange(), ["front"], section=True)
        dims, _, _ = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        assert by_name["length"].view == "section_A"
        assert by_name["length"].value == 40.0
        assert by_name["step_1"].value == 15.0
        bore = by_name["bore"]
        assert bore.view == "section_A"
        assert bore.value == 30.0
        assert bore.prefix == "<MOD-DIAM>"
        # bore picks: the two internal profile lines at y = +-15, z collapsed
        sec = d.views["section_A"]
        ys = sorted(p[1] for p in bore.picks)
        assert ys[0] == pytest.approx(sec.center[1] - 15 * sec.scale)
        assert ys[1] == pytest.approx(sec.center[1] + 15 * sec.scale)

    def test_step_pick_lands_on_the_annular_face(self) -> None:
        d = drawing_for(make_flange(), ["front"], section=True)
        dims, _, _ = analyze(d)
        step = next(dim for dim in dims if dim.name == "step_1")
        sec = d.views["section_A"]
        # step face at z=15 is the annulus r in [30, 60]: pick v = 45
        v = (step.picks[1][1] - sec.center[1]) / sec.scale
        assert v == pytest.approx(45.0)

    def test_no_section_falls_back_with_warning(self) -> None:
        d = drawing_for(make_flange(), ["front", "top", "right"])
        dims, _, warnings = analyze(d)
        by_name = {dim.name: dim for dim in dims}
        assert any("no section view" in w for w in warnings)
        assert by_name["length"].view in ("right", "top")
        assert by_name["bore"].kind == "diameter"  # dimensioned on the true view

    def test_bore_excluded_from_hole_callouts(self) -> None:
        d = drawing_for(make_flange(), ["front"], section=True)
        dims, _, _ = analyze(d)
        callouts = [dim for dim in dims if dim.name.startswith("callout_")]
        assert len(callouts) == 1  # bolt holes only; the centered bore is not one


class TestNotes:
    def test_fillet_chamfer_slot_notes(self) -> None:
        part = ModelTracker()
        part.new_part()
        part.create_sketch("front")
        part.draw_rectangle((0, 0), 120, 80)
        part.extrude(12, False)
        part.fillet(8, "vertical_corners", None, None)
        part.chamfer(1, 45, "top_loop", "Boss-Extrude1", None)
        part.create_sketch("front")
        part.draw_slot((-20, 20), (20, 20), 10)
        part.cut_extrude(True, None, False, None)
        part.save_part("p.SLDPRT")
        part.pop_warnings()
        d = drawing_for(part, ["front", "top", "right"])
        _, notes, _ = analyze(d)
        texts = [n.text for n in notes]
        assert "FILLETS R8 (4 PLCS)" in texts
        assert "CHAMFERS 1 X 45<MOD-DEG> (4 PLCS)" in texts
        assert "SLOT W10 X 40 C-C" in texts

    def test_notes_stack_downward_under_front_view(self) -> None:
        d = drawing_for(make_bracket(), ["front", "top", "right"])
        _, notes, _ = analyze(d)
        ys = [n.position[1] for n in notes]
        assert ys == sorted(ys, reverse=True)
