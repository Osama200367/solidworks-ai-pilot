# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning the confirmed v1.0 adversarial-review findings.

Each test fails against the pre-fix code and passes after it. The findings
were weighted on extraction robustness, repair correctness and the
never-execute-unvalidated safety guarantee.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from swpilot.llm.client import LLMConfig, LLMRequestError, OpenAICompatibleClient
from swpilot.llm.extract import extract_json
from swpilot.llm.vocabulary import vocabulary_text


class TestExtractionRobustness:
    def test_stray_inch_mark_quote_in_prose_does_not_drop_the_object(self) -> None:
        # finding: string-state tracking ran over prose, so a lone `"` (an
        # inch mark, ubiquitous in this dimension-driven domain) desynced the
        # scanner and swallowed the real JSON object.
        resp = 'Here is a 3" flange:\n{"schema_version":"0.5","commands":[{"op":"new_part"}]}'
        obj = extract_json(resp)
        assert obj["commands"][0]["op"] == "new_part"  # type: ignore[index]

    def test_odd_quote_count_in_prose(self) -> None:
        # several stray quotes (still odd/even mixes) must not matter at depth 0
        resp = 'A 1/2" bolt, 3" long, tapped 1/4":\n{"schema_version":"0.5","commands":[]}'
        assert extract_json(resp)["schema_version"] == "0.5"

    def test_last_bare_object_wins_over_echoed_example(self) -> None:
        # finding: spans were sorted largest-first, so a longer echoed example
        # beat the model's shorter real (last) answer.
        resp = (
            'Following the example {"schema_version":"0.5","commands":['
            '{"op":"create_plate","width":120,"height":80,"thickness":12},'
            '{"op":"save_part","path":"base.SLDPRT"}]} '
            'here is your answer: '
            '{"schema_version":"0.5","commands":[{"op":"new_part"}]}'
        )
        obj = extract_json(resp)
        assert len(obj["commands"]) == 1  # type: ignore[arg-type]
        assert obj["commands"][0]["op"] == "new_part"  # type: ignore[index]

    def test_decoy_commands_key_that_is_not_a_list_is_skipped(self) -> None:
        # finding: the preference loop accepted any "commands" key, so a decoy
        # {"commands": "..."} shadowed a genuinely valid CommandFile.
        resp = (
            '{"commands":"' + "x" * 80 + '"} '
            '{"schema_version":"0.5","commands":[{"op":"new_part"}]}'
        )
        obj = extract_json(resp)
        assert obj["schema_version"] == "0.5"
        assert isinstance(obj["commands"], list)


class TestVocabularyRendering:
    def test_no_pydantic_undefined_sentinel_leaks_into_the_prompt(self) -> None:
        # finding: default_factory fields (e.g. insert_component.rotate)
        # rendered the literal "PydanticUndefined" as their default.
        v = vocabulary_text()
        assert "PydanticUndefined" not in v

    def test_rotate_factory_default_renders_as_empty_list(self) -> None:
        line = next(
            ln.strip() for ln in vocabulary_text().splitlines()
            if ln.strip().startswith("rotate?")
        )
        assert "(default [])" in line

    def test_scale_ratio_is_not_labelled_a_millimetre_point(self) -> None:
        # finding: tuple[PositiveInt, PositiveInt] collapsed to "[x, y]", which
        # the legend defines as "a 2D point (mm)".
        v = vocabulary_text()
        scale_lines = [ln.strip() for ln in v.splitlines() if ln.strip().startswith("scale?")]
        assert scale_lines  # the drawing ops expose a scale field
        for ln in scale_lines:
            assert "[integer, integer]" in ln
            assert "[x, y]" not in ln

    def test_real_mm_points_still_render_as_xy(self) -> None:
        # the fix must not regress genuine float coordinate tuples
        v = vocabulary_text()
        assert any(
            ln.strip().startswith("center?") and "[x, y]" in ln
            for ln in v.splitlines()
        )


class TestClientNullContent:
    def _client(self) -> OpenAICompatibleClient:
        return OpenAICompatibleClient(
            LLMConfig(base_url="http://localhost/v1", model="m", api_key="")
        )

    def _patch(self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
        class _Resp:
            def __init__(self, d: bytes) -> None:
                self._d = d

            def read(self) -> bytes:
                return self._d

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: object) -> bool:
                return False

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda *a, **k: _Resp(json.dumps(payload).encode("utf-8")),
        )

    def test_null_content_raises_instead_of_returning_the_string_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # finding: a null content field str()'d into "None" and was fed to the
        # parser, producing an opaque failure + wasted repair.
        self._patch(monkeypatch, {"choices": [{"message": {"content": None}}]})
        with pytest.raises(LLMRequestError):
            self._client().complete("hi")

    def test_normal_content_still_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
        assert self._client().complete("hi") == "{}"
