# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""End-to-end acceptance for v0.3: the bolted_cover assembly."""

from pathlib import Path

import pytest

from swpilot.backends.calls import SW_MATE_TYPES
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_bolted_cover():
    _, expanded = load_and_expand(EXAMPLES / "bolted_cover.json")
    backend = MockBackend()
    report = execute(expanded, backend, schema_version="0.3")
    assert report.success, [r.error for r in report.results if r.error]
    return report, backend


class TestBoltedCover:
    def test_documents_and_components(self) -> None:
        report, _ = run_bolted_cover()
        docs = report.final_state["documents"]
        assert [d["document"] for d in docs] == ["base", "cover", "m8_bolt", "bolted_cover"]
        asm = docs[3]
        assert asm["kind"] == "assembly"
        names = [c["name"] for c in asm["components"]]
        assert names == ["base_1", "cover_1", "bolt_1", "bolt_2", "bolt_3", "bolt_4"]
        assert asm["components"][0]["fixed"] is True
        assert len(asm["mates"]) == 11  # 3 cover mates + 4x(concentric+seat)

    def test_cover_fully_constrained_bolts_spin_only(self) -> None:
        report, _ = run_bolted_cover()
        asm = report.final_state["documents"][3]
        cover = next(c for c in asm["components"] if c["name"] == "cover_1")
        assert cover["free_translations"] == [] and cover["free_rotations"] == []
        for i in range(1, 5):
            bolt = next(c for c in asm["components"] if c["name"] == f"bolt_{i}")
            assert bolt["free_translations"] == []
            assert bolt["free_rotations"] == ["z"]

    def test_bolts_snapped_to_hole_positions(self) -> None:
        report, _ = run_bolted_cover()
        asm = report.final_state["documents"][3]
        positions = {
            tuple(c["translation"])
            for c in asm["components"]
            if c["name"].startswith("bolt_")
        }
        assert positions == {
            (-45.0, -25.0, 20.0),
            (45.0, -25.0, 20.0),
            (-45.0, 25.0, 20.0),
            (45.0, 25.0, 20.0),
        }

    def test_spin_warnings_marked_normal(self) -> None:
        report, _ = run_bolted_cover()
        save_result = [r for r in report.results if r.op == "save_assembly"][0]
        spin = [w for w in save_result.warnings if "normal for fasteners" in w]
        assert len(spin) == 4
        assert not any("under-constrained" in w for w in save_result.warnings)


class TestBoltedCoverCallPlan:
    def test_document_switching_calls(self) -> None:
        _, backend = run_bolted_cover()
        activations = [c.args[0] for c in backend.call_log if c.method == "ActivateDoc2"]
        # parts are built in order with no switching; only the bolt_circle's
        # inserts stay in the (already active) assembly — so no activations
        # are needed at all in this linear file.
        assert activations == []
        new_docs = [c for c in backend.call_log if c.method == "NewDocument"]
        assert len(new_docs) == 4  # base, cover, m8_bolt, assembly

    def test_component_inserts(self) -> None:
        _, backend = run_bolted_cover()
        inserts = [c.args for c in backend.call_log if c.method == "AddComponent5"]
        assert len(inserts) == 6
        assert inserts[0][0] == "base.SLDPRT"
        # cover inserted with a 20mm standoff -> meters (the mate closes it)
        assert inserts[1][5:] == (0.0, 0.0, pytest.approx(0.020))
        # bolts inserted 2mm proud of the seat: z = 20 + 2 = 22mm
        assert all(i[7] == pytest.approx(0.022) for i in inserts[2:])
        renames = [c.value for c in backend.call_log if c.method == "Name2"]
        assert renames == ["base_1", "cover_1", "bolt_1", "bolt_2", "bolt_3", "bolt_4"]

    def test_fix_component_only_for_base(self) -> None:
        _, backend = run_bolted_cover()
        fixes = [c for c in backend.call_log if c.method == "FixComponent"]
        assert len(fixes) == 1
        fix_selects = [
            c.args[0]
            for c in backend.call_log
            if c.method == "SelectByID2" and c.args[1] == "COMPONENT"
        ]
        assert fix_selects == ["base_1@<asm>"]

    def test_mate_calls_use_documented_enums(self) -> None:
        _, backend = run_bolted_cover()
        mates = [c.args for c in backend.call_log if c.method == "AddMate5"]
        assert len(mates) == 11
        types = [m[0] for m in mates]
        assert types.count(SW_MATE_TYPES["coincident"]) == 5  # 1 cover + 4 seats
        assert types.count(SW_MATE_TYPES["concentric"]) == 6  # 2 cover + 4 bolts
        assert all(len(m) == 15 for m in mates)

    def test_mate_face_picks_in_meters(self) -> None:
        _, backend = run_bolted_cover()
        face_selects = [
            c.args
            for c in backend.call_log
            if c.method == "SelectByID2" and c.args[1] == "FACE"
        ]
        # Picks are PRE-solve: the cover still sits at its standoff (bottom
        # face z = 20mm) while the base top is at 12mm — AddMate5 itself
        # closes the gap on Windows, so the selections must use these.
        assert face_selects[0][4] == pytest.approx(0.020)
        assert face_selects[1][4] == pytest.approx(0.012)

    def test_mates_renamed(self) -> None:
        _, backend = run_bolted_cover()
        renames = [
            c.value
            for c in backend.call_log
            if c.target == "LastFeature" and str(c.value).startswith("Mate")
        ]
        assert renames == [f"Mate{i}" for i in range(1, 12)]

    def test_save_assembly_call(self) -> None:
        _, backend = run_bolted_cover()
        saves = [c.args[0] for c in backend.call_log if c.method == "SaveAs3"]
        assert saves == [
            "base.SLDPRT",
            "cover.SLDPRT",
            "m8_bolt.SLDPRT",
            "bolted_cover.SLDASM",
        ]
