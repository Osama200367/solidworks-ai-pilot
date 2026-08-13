"""Tests for the natural-language layer (v1.0), with recorded LLM fixtures.

The whole pipeline — bundle → extract → validate → (one) repair → expand →
execute — is proven with canned LLM responses. We never call a live model
(that's the model's job); we prove OUR path is correct and safe.
"""

import json
from pathlib import Path

import pytest

from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import expand_commands
from swpilot.commands.schema import MACRO_OPS, PRIMITIVE_OPS
from swpilot.executor import execute
from swpilot.llm import (
    ExtractionError,
    build_bundle,
    build_repair_prompt,
    extract_json,
    validate_or_repair,
)
from swpilot.llm.vocabulary import all_ops, vocabulary_text

EXAMPLES = Path(__file__).parent.parent / "examples"


def _example_json(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


class TestVocabulary:
    def test_every_op_documented(self) -> None:
        # a future command can't be silently missing from the AI vocabulary
        assert all_ops() >= (PRIMITIVE_OPS | MACRO_OPS)

    def test_vocabulary_mentions_key_macros_and_units(self) -> None:
        v = vocabulary_text()
        for op in ("create_plate", "involute_spur_gear", "hole", "bolt_circle", "mate"):
            assert f'op "{op}"' in v
        assert "MILLIMETERS" in v
        assert "English" in v  # keys are always English

    def test_enum_values_rendered(self) -> None:
        v = vocabulary_text()
        # mate types appear as a literal enum list
        assert '"coincident"' in v and '"concentric"' in v


# --------------------------------------------------------------------------
# Prompt bundle
# --------------------------------------------------------------------------


class TestBundle:
    def test_bundle_has_vocab_examples_and_request(self) -> None:
        b = build_bundle("a 50mm cube")
        assert "COMMAND VOCABULARY" in b
        assert "EXAMPLES:" in b
        assert "a 50mm cube" in b
        # the Arabic few-shot request is present
        assert "بدي ترس" in b

    def test_bundle_is_model_agnostic_text(self) -> None:
        # no provider-specific tokens; just plain instructions + JSON
        b = build_bundle("x")
        assert "role" not in b.lower().split("\n")[0]  # not a chat-message blob


# --------------------------------------------------------------------------
# Extraction (defensive)
# --------------------------------------------------------------------------


class TestExtraction:
    def test_bare_object(self) -> None:
        obj = extract_json('{"schema_version":"0.5","commands":[]}')
        assert obj["schema_version"] == "0.5"

    def test_fenced_block(self) -> None:
        resp = 'Here:\n```json\n{"schema_version":"0.5","commands":[{"op":"new_part"}]}\n```\ndone'
        obj = extract_json(resp)
        assert obj["commands"][0]["op"] == "new_part"  # type: ignore[index]

    def test_prose_wrapped_object(self) -> None:
        resp = 'Sure, this makes it: {"schema_version":"0.5","commands":[{"op":"new_part"}]} enjoy!'
        assert extract_json(resp)["commands"]  # type: ignore[index]

    def test_prefers_commandfile_over_stray_object(self) -> None:
        # a stray {} before the real answer must not win
        resp = '{"note":"thinking"} then {"schema_version":"0.5","commands":[{"op":"new_part"}]}'
        assert "commands" in extract_json(resp)

    def test_strings_with_braces_do_not_break_parsing(self) -> None:
        resp = '{"schema_version":"0.5","commands":[{"op":"save_part","path":"a{b}.SLDPRT"}]}'
        assert extract_json(resp)["commands"][0]["path"] == "a{b}.SLDPRT"  # type: ignore[index]

    def test_prose_only_raises(self) -> None:
        with pytest.raises(ExtractionError):
            extract_json("I'm sorry, I can't help with that.")

    def test_empty_raises(self) -> None:
        with pytest.raises(ExtractionError):
            extract_json("   ")


# --------------------------------------------------------------------------
# Validate + repair
# --------------------------------------------------------------------------


class TestValidateOrRepair:
    def test_valid_response_passes(self) -> None:
        out = validate_or_repair("plate", _example_json("plate_with_holes.json"))
        assert out.ok and out.command_file is not None
        assert not out.repaired

    def test_invalid_no_retry_returns_repair_prompt(self) -> None:
        bad = '{"schema_version":"0.5","commands":[{"op":"create_plate","width":100}]}'
        out = validate_or_repair("plate", bad)
        assert not out.ok
        assert out.errors and "height" in out.errors
        assert out.repair_prompt is not None
        assert "VALIDATION ERRORS" in out.repair_prompt

    def test_one_repair_fixes_it(self) -> None:
        bad = '{"schema_version":"0.5","commands":[{"op":"create_plate","width":100}]}'
        fixed = (
            '{"schema_version":"0.5","commands":['
            '{"op":"create_plate","width":100,"height":50,"thickness":10}]}'
        )
        calls: list[str] = []

        def retry(prompt: str) -> str:
            calls.append(prompt)
            return fixed

        out = validate_or_repair("plate", bad, retry=retry)
        assert out.ok and out.repaired
        assert len(calls) == 1  # exactly one repair attempt

    def test_repair_only_runs_once(self) -> None:
        bad = '{"schema_version":"0.5","commands":[{"op":"create_plate","width":100}]}'
        calls: list[str] = []

        def retry(prompt: str) -> str:
            calls.append(prompt)
            return bad  # model fails to fix it

        out = validate_or_repair("plate", bad, retry=retry)
        assert not out.ok
        assert len(calls) == 1  # not retried again
        assert out.repaired  # a repair was attempted

    def test_extraction_failure_yields_repair_prompt(self) -> None:
        out = validate_or_repair("plate", "sorry, no JSON here")
        assert not out.ok
        assert out.repair_prompt is not None


# --------------------------------------------------------------------------
# Acceptance: full pipeline through the mock
# --------------------------------------------------------------------------


def _run_through_mock(command_file) -> object:
    expanded = expand_commands(list(command_file.commands))
    return execute(expanded, MockBackend())


class TestAcceptance:
    def test_arabic_gear_runs_1_of_1(self) -> None:
        # recorded LLM response for "بدي ترس m2 بـ20 سن مع تجويف 16 وخابور",
        # prose-wrapped as a free model would return it
        response = (
            "تمام! هاي ملف الأوامر:\n```json\n"
            + json.dumps(
                {
                    "schema_version": "0.5",
                    "commands": [
                        {
                            "op": "involute_spur_gear",
                            "name": "gear",
                            "module": 2,
                            "teeth": 20,
                            "bore": 16,
                            "face_width": 20,
                            "keyway": {"width": 5, "depth": 2.3},
                        },
                        {"op": "save_part", "path": "gear.SLDPRT"},
                    ],
                }
            )
            + "\n```"
        )
        out = validate_or_repair("بدي ترس m2 بـ20 سن مع تجويف 16 وخابور", response)
        assert out.ok
        assert out.command_file.commands[0].op == "involute_spur_gear"  # type: ignore[union-attr]
        report = _run_through_mock(out.command_file)
        assert report.success  # type: ignore[attr-defined]
        # 1/1 at the source-command level (the gear macro + save_part)
        assert len(out.command_file.commands) == 2  # type: ignore[union-attr]

    def test_assembly_request(self) -> None:
        out = validate_or_repair("base + cover + bolts", _example_json("bolted_cover.json"))
        assert out.ok
        report = _run_through_mock(out.command_file)
        assert report.success  # type: ignore[attr-defined]
        docs = report.final_state["documents"]  # type: ignore[attr-defined]
        assert any(d.get("kind") == "assembly" for d in docs)

    def test_plain_plate(self) -> None:
        response = json.dumps(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
                    {"op": "add_corner_holes", "diameter": 8, "margin": 10},
                    {"op": "save_part", "path": "plate.SLDPRT"},
                ],
            }
        )
        out = validate_or_repair("100x50x10 plate with 4 corner holes", response)
        assert out.ok
        assert _run_through_mock(out.command_file).success  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Safety: nothing invalid ever reaches execution
# --------------------------------------------------------------------------


class TestSafety:
    def test_invalid_never_produces_a_command_file(self) -> None:
        # an op that does not exist must fail validation, never execute
        bad = '{"schema_version":"0.5","commands":[{"op":"delete_everything"}]}'
        out = validate_or_repair("hack", bad)
        assert out.command_file is None

    def test_repair_prompt_echoes_errors_and_request(self) -> None:
        p = build_repair_prompt("my request", '{"bad":true}', "some error")
        assert "my request" in p and "some error" in p and '{"bad":true}' in p
