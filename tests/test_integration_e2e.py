# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Final integration audit: cross-phase end-to-end seams (v0.1 → v1.1).

Every phase passed its own adversarial review; these tests prove the SEAMS —
that voice, the LLM layer, the command engine, macros, the twin, and the mock
backend function together as one system. Recorded fixtures only; no live
model, no hardware.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from typer.testing import CliRunner

import swpilot
from swpilot.backends.mock.simulator import MockBackend
from swpilot.cli import app
from swpilot.commands import schema as sc
from swpilot.commands.loader import expand_commands, parse_command_data
from swpilot.executor import execute
from swpilot.llm import build_bundle, validate_or_repair
from swpilot.llm.vocabulary import all_ops
from swpilot.voice import normalize

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"
runner = CliRunner()


def _mock_run(command_file) -> object:  # noqa: ANN001 - CommandFile
    expanded = expand_commands(list(command_file.commands))
    return execute(expanded, MockBackend())


def _fenced(payload: dict) -> str:
    """A recorded LLM reply, prose-wrapped the way free models answer."""
    return "تمام! هذا ملف الأوامر:\n```json\n" + json.dumps(payload) + "\n```"


GEAR_PAIR_ASSEMBLY = {
    "schema_version": "0.5",
    "commands": [
        {"op": "involute_spur_gear", "name": "pinion", "module": 2, "teeth": 20,
         "face_width": 20, "bore": 16, "keyway": {"width": 5, "depth": 2.3}},
        {"op": "save_part", "path": "pinion.SLDPRT"},
        {"op": "involute_spur_gear", "name": "wheel", "module": 2, "teeth": 40,
         "face_width": 20, "bore": 20},
        {"op": "save_part", "path": "wheel.SLDPRT"},
        {"op": "new_assembly", "name": "gear_pair"},
        {"op": "insert_component", "part": "pinion", "name": "pinion_1", "fixed": True},
        {"op": "insert_component", "part": "wheel", "name": "wheel_1", "at": [60, 0, 0]},
        {"op": "gear_mesh_check", "a": "pinion_1", "b": "wheel_1",
         "expected_center_distance": 60},
        {"op": "save_assembly", "path": "gear_pair.SLDASM"},
    ],
}

PLATE_DRAWING = {
    "schema_version": "0.5",
    "commands": [
        {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
        {"op": "add_corner_holes", "diameter": 8, "margin": 10},
        {"op": "save_part", "path": "plate.SLDPRT"},
        {"op": "create_drawing", "name": "plate_sheet", "of": "Part1", "sheet": "A4",
         "title": "PLATE 100x50"},
        {"op": "standard_views"},
        {"op": "smart_dimensions"},
        {"op": "save_drawing", "path": "plate.SLDDRW"},
    ],
}

# v0.5 gear + v0.3 assembly + v0.4 drawing in ONE command file — the deepest
# cross-phase chain the engine supports.
GEAR_ASSEMBLY_DRAWING = {
    "schema_version": "0.5",
    "commands": [
        *GEAR_PAIR_ASSEMBLY["commands"],
        {"op": "create_drawing", "name": "pinion_sheet", "of": "pinion", "sheet": "A3",
         "title": "PINION m2 z20"},
        {"op": "standard_views"},
        {"op": "isometric_view", "corner": "top_right"},
        {"op": "smart_dimensions"},
        {"op": "save_drawing", "path": "pinion.SLDDRW"},
    ],
}


# --------------------------------------------------------------------------
# Voice → normalize → (recorded LLM) → validate → mock execute
# --------------------------------------------------------------------------


class TestVoiceToMockChains:
    def test_arabic_voice_part_chain(self) -> None:
        spoken = "بدي لوح مئة في خمسين تخانة عشرة مع اربع ثقوب بالزوايا"
        normalized = normalize(spoken)
        # the dialect layer did its job before the LLM ever sees the text
        assert "100" in normalized.split() and "50" in normalized.split()
        assert "سماكة" in normalized  # dialect تخانة → canonical
        bundle = build_bundle(normalized)
        assert normalized in bundle  # request embedded verbatim in the prompt
        out = validate_or_repair(normalized, _fenced(PLATE_DRAWING))
        assert out.ok
        report = _mock_run(out.command_file)
        assert report.success  # type: ignore[attr-defined]

    def test_arabic_voice_gear_pair_assembly_chain(self) -> None:
        spoken = "بدي ترسين موديول اثنين عشرين سن واربعين سن يتعاشقان في مجموعة"
        normalized = normalize(spoken)
        # module 2 / 20 teeth / 40 teeth all distinct — never merged
        parts = normalized.split()
        assert "2" in parts and "20" in parts
        assert "22" not in parts and "60" not in parts
        out = validate_or_repair(normalized, _fenced(GEAR_PAIR_ASSEMBLY))
        assert out.ok
        report = _mock_run(out.command_file)
        assert report.success  # type: ignore[attr-defined]
        docs = report.final_state["documents"]  # type: ignore[attr-defined]
        assert any(d.get("kind") == "assembly" for d in docs)

    def test_arabic_voice_drawing_chain(self) -> None:
        spoken = "اعمل لوحة رسم للقطعة مع الابعاد"
        out = validate_or_repair(normalize(spoken), _fenced(PLATE_DRAWING))
        assert out.ok
        report = _mock_run(out.command_file)
        assert report.success  # type: ignore[attr-defined]
        docs = report.final_state["documents"]  # type: ignore[attr-defined]
        assert any(d.get("kind") == "drawing" for d in docs)


# --------------------------------------------------------------------------
# The deepest chain: one CommandFile spanning gears + assembly + drawing
# --------------------------------------------------------------------------


class TestCrossPhaseSingleFile:
    def test_gear_assembly_drawing_in_one_file(self) -> None:
        cf = parse_command_data(GEAR_ASSEMBLY_DRAWING)
        expanded = expand_commands(list(cf.commands))
        report = execute(expanded, MockBackend())
        assert report.success  # type: ignore[attr-defined]
        assert all(r.status == "ok" for r in report.results)
        kinds = [d.get("kind") for d in report.final_state["documents"]]  # type: ignore[attr-defined]
        # two gear parts, the meshed assembly, and the dimensioned sheet
        assert kinds.count("part") == 2
        assert "assembly" in kinds and "drawing" in kinds

    def test_same_file_via_llm_layer(self) -> None:
        # identical content arriving through the LLM path must behave the same
        out = validate_or_repair("gear pair + drawing", _fenced(GEAR_ASSEMBLY_DRAWING))
        assert out.ok
        assert _mock_run(out.command_file).success  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Safety gate: every entry path shows the command list / honors the confirm
# --------------------------------------------------------------------------


class TestEntryPathSafetyGate:
    def test_ai_apply_shows_command_list_before_execution(self, tmp_path: Path) -> None:
        reply = tmp_path / "reply.json"
        reply.write_text(json.dumps(PLATE_DRAWING), encoding="utf-8")
        result = runner.invoke(app, ["ai-apply", str(reply)])
        assert result.exit_code == 0
        # the parsed list precedes the execution summary in the output
        assert result.output.index("parsed") < result.output.index("commands:")

    def test_ai_apply_solidworks_declined_executes_nothing(self, tmp_path: Path) -> None:
        reply = tmp_path / "reply.json"
        reply.write_text(json.dumps(PLATE_DRAWING), encoding="utf-8")
        result = runner.invoke(
            app, ["ai-apply", str(reply), "--backend", "solidworks"], input="n\n"
        )
        # declining aborts BEFORE any COM import (which would exit 1 on Linux)
        assert result.exit_code == 0
        assert "aborted (nothing executed)" in result.output

    def test_voice_text_solidworks_declined_executes_nothing(
        self, monkeypatch,  # noqa: ANN001
    ) -> None:
        # api-mode voice path converges on the same gate; fake the LLM reply
        monkeypatch.setenv("SWPILOT_LLM_MODEL", "test-model")
        monkeypatch.setattr(
            "swpilot.llm.client.OpenAICompatibleClient.complete",
            lambda self, prompt: json.dumps(PLATE_DRAWING),
        )
        result = runner.invoke(
            app,
            ["voice", "--text", "a plate", "--mode", "api", "--backend", "solidworks"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "aborted (nothing executed)" in result.output

    def test_ai_copy_paste_never_executes(self) -> None:
        result = runner.invoke(app, ["ai", "a 50mm cube"])
        assert result.exit_code == 0
        assert "COMMAND VOCABULARY" in result.output
        assert "backend:" not in result.output  # no execution summary

    def test_invalid_reply_never_reaches_backend(self, tmp_path: Path) -> None:
        reply = tmp_path / "bad.json"
        reply.write_text('{"schema_version":"0.5","commands":[{"op":"nuke_it"}]}')
        result = runner.invoke(app, ["ai-apply", str(reply)])
        assert result.exit_code == 2
        assert "did not validate" in result.output
        assert "backend:" not in result.output


# --------------------------------------------------------------------------
# Contract & version consistency
# --------------------------------------------------------------------------


class TestContractConsistency:
    def test_schema_and_package_versions(self) -> None:
        assert sc.SCHEMA_VERSION == "0.5"  # the voice/LLM layers add no commands
        assert swpilot.__version__ == "1.1.0"
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["version"] == swpilot.__version__
        extras = pyproject["project"]["optional-dependencies"]
        assert "voice" in extras and "windows" in extras

    def test_docs_reference_current_version(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        assert "v1.1" in readme
        assert "### v1.1 — Voice layer ◀ current phase" in roadmap

    def test_vocabulary_covers_every_engine_op(self) -> None:
        # the LLM can be told about every op the engine can execute
        assert all_ops() >= (sc.PRIMITIVE_OPS | sc.MACRO_OPS)
        # and documents nothing the engine doesn't declare
        assert all_ops() <= (sc.PRIMITIVE_OPS | sc.MACRO_OPS)

    def test_every_example_file_still_validates_and_runs(self) -> None:
        for name in sorted(EXAMPLES.glob("*.json")):
            if name.name.endswith(".report.json"):
                continue
            data = json.loads(name.read_text(encoding="utf-8"))
            cf = parse_command_data(data)
            report = _mock_run(cf)
            assert report.success, f"{name.name} failed in the mock"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Failure-mode & repair seams after integration
# --------------------------------------------------------------------------


class TestFailureRepairSeams:
    def test_unknown_op_yields_repair_prompt(self) -> None:
        bad = '{"schema_version":"0.5","commands":[{"op":"teleport_part"}]}'
        out = validate_or_repair("x", bad)
        assert not out.ok and out.command_file is None
        assert out.repair_prompt is not None

    def test_valid_json_bad_params_repairs(self) -> None:
        bad = json.dumps(
            {"schema_version": "0.5",
             "commands": [{"op": "involute_spur_gear", "module": -2, "teeth": 20}]}
        )
        fixed = json.dumps(
            {"schema_version": "0.5",
             "commands": [{"op": "involute_spur_gear", "module": 2, "teeth": 20,
                           "face_width": 20, "bore": 16},
                          {"op": "save_part", "path": "g.SLDPRT"}]}
        )
        out = validate_or_repair("gear", bad, retry=lambda prompt: fixed)
        assert out.ok and out.repaired
        assert _mock_run(out.command_file).success  # type: ignore[attr-defined]

    def test_inch_mark_prose_still_extracts(self) -> None:
        # v1.0 regression, re-proven through the whole validate path
        reply = 'Here is a 3" part:\n' + json.dumps(PLATE_DRAWING)
        out = validate_or_repair("plate", reply)
        assert out.ok

    def test_normalization_edges_hold_through_bundle(self) -> None:
        # the two cardinal Arabic cases, surviving into the prompt bundle
        merged = normalize("خمسة وعشرين")
        split = normalize("اثنين عشرين")
        assert merged == "25"
        assert split == "2 20"
        assert "2 20" in build_bundle(split)
