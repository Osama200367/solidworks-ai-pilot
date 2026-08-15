# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Robust natural-language understanding (v1.2): messy-case fixture table.

Pins Part 1 of Phase G: the computed coming-soon catalog (with its
drift-guard), deterministic out-of-scope detection, the "skipped" partial-
understanding envelope, and a table of recorded fixtures for dialect,
compound, mixed-language, out-of-scope and ambiguous requests. No live
model is ever called.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from swpilot.backends.mock.simulator import MockBackend
from swpilot.cli import app
from swpilot.commands.loader import expand_commands
from swpilot.commands.schema import MACRO_OPS, PRIMITIVE_OPS
from swpilot.executor import execute
from swpilot.llm import build_bundle, validate_or_repair
from swpilot.llm.examples import few_shots
from swpilot.llm.features import (
    CATALOG,
    coming_soon,
    coming_soon_message,
    detect_unsupported,
    implemented_ops,
    supported_features,
)
from swpilot.voice import normalize

runner = CliRunner()


def _mock_run(command_file) -> object:  # noqa: ANN001
    return execute(expand_commands(list(command_file.commands)), MockBackend())


# --------------------------------------------------------------------------
# The drift guard: supported/coming-soon is COMPUTED, never hand-maintained
# --------------------------------------------------------------------------


class TestComingSoonDriftGuard:
    def test_no_implemented_op_is_ever_coming_soon(self) -> None:
        # THE invariant: the moment an op ships in the Command union, its
        # feature stops being "coming soon" automatically.
        cs_keys = {f.key for f in coming_soon()}
        assert not (cs_keys & implemented_ops())

    def test_every_coming_soon_op_is_really_absent(self) -> None:
        for feat in coming_soon():
            assert feat.key not in implemented_ops()

    def test_supported_and_coming_soon_partition_the_catalog(self) -> None:
        sup = {f.key for f in supported_features()}
        cs = {f.key for f in coming_soon()}
        assert sup | cs == {f.key for f in CATALOG}
        assert not (sup & cs)

    def test_implemented_ops_tracks_the_live_union(self) -> None:
        assert implemented_ops() == (PRIMITIVE_OPS | MACRO_OPS)

    def test_catalog_covers_the_headline_missing_features(self) -> None:
        cs = {f.key for f in coming_soon()}
        assert {"sweep", "loft", "shell", "rib", "draft", "sheet_metal"} <= cs


# --------------------------------------------------------------------------
# Deterministic out-of-scope detection + the bilingual message
# --------------------------------------------------------------------------


class TestOutOfScopeDetection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a swept boss along a helix path", {"sweep"}),
            ("loft between the two profiles", {"loft"}),
            ("shell it to 2mm walls", {"shell"}),
            ("بدي قطعة صاج مع ثني", {"sheet_metal"}),
            ("بدي ترس حلزوني موديول 2", {"helical_gear"}),
            ("ترس مخروطي 30 سن", {"bevel_gear"}),
            ("mirror the pocket to the other side", {"mirror"}),
            ("add a rib under the boss and loft the top", {"rib", "loft"}),
        ],
    )
    def test_unsupported_detected(self, text: str, expected: set[str]) -> None:
        assert {f.key for f in detect_unsupported(text)} == expected

    @pytest.mark.parametrize(
        "text",
        [
            "a plate with M8 holes and a chamfer",  # all supported
            "بدي ترس عدل بعشرين سن مع خابور",  # supported gear
            "a revolve with a cosmetic thread",  # supported curve ops
            "",  # empty
            "   ",
        ],
    )
    def test_supported_requests_trigger_nothing(self, text: str) -> None:
        assert detect_unsupported(text) == ()

    def test_message_is_bilingual_and_warm(self) -> None:
        sweep = next(f for f in coming_soon() if f.key == "sweep")
        msg = coming_soon_message(sweep)
        assert "مش متوفرة بالنسخة الحالية" in msg
        assert "إن شاء الله" in msg and "🔜" in msg
        assert "isn't available in the current version" in msg
        assert "alternative" in msg  # offers a supported route

    def test_every_coming_soon_feature_has_an_alternative(self) -> None:
        for feat in coming_soon():
            assert feat.alternative_en and feat.alternative_ar, feat.key

    def test_cli_warns_but_never_crashes(self) -> None:
        result = runner.invoke(
            app, ["ai", "sheet metal bracket with a swept flange", "--mode", "copy-paste"]
        )
        assert result.exit_code == 0  # never an error, never stuck
        assert "sheet metal" in result.output
        assert "مش متوفرة" in result.output
        assert "COMMAND VOCABULARY" in result.output  # still proceeds


# --------------------------------------------------------------------------
# Partial understanding: the "skipped" envelope
# --------------------------------------------------------------------------


class TestSkippedEnvelope:
    def test_skipped_is_stripped_and_surfaced(self) -> None:
        resp = json.dumps(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10}
                ],
                "skipped": ["the bend on the long edge (sheet metal)"],
            }
        )
        out = validate_or_repair("plate with a bend", resp)
        assert out.ok  # extra field never reaches the extra="forbid" schema
        assert out.skipped == ["the bend on the long edge (sheet metal)"]

    def test_no_skipped_means_empty_list(self) -> None:
        resp = json.dumps(
            {"schema_version": "0.5",
             "commands": [{"op": "create_plate", "width": 10, "height": 10, "thickness": 2}]}
        )
        assert validate_or_repair("x", resp).skipped == []

    def test_hostile_skipped_is_sanitized(self) -> None:
        resp = json.dumps(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "create_plate", "width": 10, "height": 10, "thickness": 2}
                ],
                "skipped": [123, "", "  ok  ", "x" * 10_000] + ["spam"] * 100,
            }
        )
        out = validate_or_repair("x", resp)
        assert out.ok
        assert "ok" in out.skipped
        assert all(len(s) <= 300 for s in out.skipped)
        assert len(out.skipped) <= 20

    def test_cli_requires_confirmation_when_skipped(self, tmp_path) -> None:  # noqa: ANN001
        reply = tmp_path / "reply.json"
        reply.write_text(
            json.dumps(
                {
                    "schema_version": "0.5",
                    "commands": [
                        {"op": "create_plate", "width": 100, "height": 50,
                         "thickness": 10}
                    ],
                    "skipped": ["الثني غير متوفر"],
                }
            ),
            encoding="utf-8",
        )
        # declining builds nothing — even with --yes, the skipped-confirm stands
        result = runner.invoke(app, ["ai-apply", str(reply), "--yes"], input="n\n")
        assert result.exit_code == 0
        assert "aborted (nothing executed)" in result.output
        # accepting builds the understood part
        result = runner.invoke(app, ["ai-apply", str(reply), "--yes"], input="y\n")
        assert result.exit_code == 0
        assert "success" in result.output

    def test_prompt_teaches_the_envelope(self) -> None:
        b = build_bundle("x")
        assert '"skipped"' in b
        assert "NEVER invent an op" in b

    def test_few_shots_include_messy_cases(self) -> None:
        shots = few_shots()
        assert len(shots) >= 5
        joined = "\n".join(s.request for s in shots)
        assert "counterbore" in joined  # implicit-standard compound example
        assert "صاج" in joined or "sheet metal" in joined  # out-of-scope example
        assert any('"skipped"' in s.json_text for s in shots)


# --------------------------------------------------------------------------
# The recorded-fixture table: messy requests → correct behavior, pinned
# --------------------------------------------------------------------------

_PLATE_CMDS = [
    {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
    {"op": "save_part", "path": "plate.SLDPRT"},
]
_GEAR_CMDS = [
    {"op": "involute_spur_gear", "module": 2, "teeth": 20, "face_width": 20, "bore": 16},
    {"op": "save_part", "path": "gear.SLDPRT"},
]

# (request, recorded LLM response dict, expected skipped fragments)
_TABLE: list[tuple[str, dict, list[str]]] = [
    # Arabic dialect, casual
    ("بدي لوح مية بخمسين تخانة عشرة",
     {"schema_version": "0.5", "commands": _PLATE_CMDS}, []),
    # compound: several features in one sentence
    ("a 120x80x12 plate, round the corners r8, four M8 counterbore holes",
     {"schema_version": "0.5", "commands": [
         {"op": "create_plate", "width": 120, "height": 80, "thickness": 12},
         {"op": "fillet", "radius": 8, "edges": {"select": "vertical_corners"}},
         {"op": "hole", "type": "counterbore", "standard": "M8",
          "at": [[-45, -25], [45, -25], [-45, 25], [45, 25]]},
         {"op": "save_part", "path": "base.SLDPRT"}]}, []),
    # mixed Arabic/English in one description
    ("بدي gear موديول 2 بعشرين tooth مع bore ستاشر",
     {"schema_version": "0.5", "commands": _GEAR_CMDS}, []),
    # implicit standard mapped to canonical
    ("plate with standard M6 clearance holes at the corners",
     {"schema_version": "0.5", "commands": [
         {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
         {"op": "hole", "standard": "M6", "at": [[-40, -15], [40, -15]]},
         {"op": "save_part", "path": "p.SLDPRT"}]}, []),
    # out-of-scope portion declared, supported portion built
    ("صفيحة 100 في 50 سماكة 10 مع ثني على الحافة",
     {"schema_version": "0.5", "commands": _PLATE_CMDS,
      "skipped": ["الثني (sheet metal) غير متوفر بالنسخة الحالية"]},
     ["الثني"]),
    # ambiguous request → model says what it assumed via skipped
    ("a bracket, you decide the details",
     {"schema_version": "0.5", "commands": _PLATE_CMDS,
      "skipped": ["assumed a plain 100x50x10 plate; no other details given"]},
     ["assumed"]),
]


class TestMessyFixtureTable:
    @pytest.mark.parametrize(("request_text", "response", "expect_skip"), _TABLE)
    def test_recorded_case(
        self, request_text: str, response: dict, expect_skip: list[str]
    ) -> None:
        # the voice normalizer must never break these requests
        normalized = normalize(request_text)
        assert normalized  # never empties a real request
        out = validate_or_repair(normalized, json.dumps(response, ensure_ascii=False))
        assert out.ok, out.errors
        for frag in expect_skip:
            assert any(frag in s for s in out.skipped)
        report = _mock_run(out.command_file)
        assert report.success  # type: ignore[attr-defined]

    def test_out_of_scope_only_request_never_yields_commands(self) -> None:
        # a request that is ENTIRELY unsupported: detection warns, and a
        # correct model emits an empty-build refusal we surface, not a crash
        feats = detect_unsupported("sweep a handle along a 3D path")
        assert [f.key for f in feats] == ["sweep"]
        # a hallucinated sweep op is refused by the validator (never executed)
        bad = json.dumps({"schema_version": "0.5",
                          "commands": [{"op": "sweep", "path": "helix"}]})
        out = validate_or_repair("sweep a handle", bad)
        assert not out.ok and out.command_file is None
