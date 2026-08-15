# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning the confirmed v1.2 adversarial-review findings.

Weighted, as the review was, on the bridge security surface (the CORS
Origin bypass), the skipped-envelope terminal-injection surface, and the
coming-soon catalog's detection precision.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from swpilot.bridge import MAX_PENDING, BridgeState, _Handler, create_server
from swpilot.commands.schema import CutExtrude, Extrude
from swpilot.llm.features import CATALOG, coming_soon, detect_unsupported
from swpilot.llm.repair import _UNSAFE_SKIPPED_CHARS, _take_skipped, validate_or_repair

GOOD = {
    "schema_version": "0.5",
    "commands": [
        {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
        {"op": "save_part", "path": "plate.SLDPRT"},
    ],
}


# --------------------------------------------------------------------------
# HIGH: the CORS Origin prefix-match bypass
# --------------------------------------------------------------------------


def _origin_ok(origin: str | None) -> bool:
    h = _Handler.__new__(_Handler)
    h.headers = {"Origin": origin} if origin is not None else {}  # type: ignore[attr-defined]
    return _Handler._origin_ok(h)


class TestOriginExactMatch:
    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost.evil.com",
            "https://127.0.0.1.evil.com",
            "http://localhostxyz.attacker.net",
            "http://127.0.0.1.attacker.io",
            "http://evil.com",
            "http://localhost@evil.com",
        ],
    )
    def test_non_local_origin_rejected(self, origin: str) -> None:
        assert _origin_ok(origin) is False

    @pytest.mark.parametrize(
        "origin",
        ["http://localhost:5173", "http://127.0.0.1:8000", "https://localhost", None],
    )
    def test_local_origin_allowed(self, origin: str | None) -> None:
        assert _origin_ok(origin) is True


class TestBridgeOriginOverHttp:
    @staticmethod
    @pytest.fixture(scope="class")
    def url() -> Iterator[str]:
        server = create_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()
        server.server_close()

    def test_lookalike_origin_gets_403(self, url: str) -> None:
        req = urllib.request.Request(
            url + "/v1/commandfile",
            data=json.dumps(GOOD).encode(),
            headers={"Content-Type": "application/json", "Origin": "http://localhost.evil.com"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 403


# --------------------------------------------------------------------------
# HIGH: skipped-envelope terminal injection (ANSI / control / bidi override)
# --------------------------------------------------------------------------


class TestSkippedSanitization:
    def test_control_ansi_and_bidi_chars_stripped(self) -> None:
        hostile = _take_skipped(
            {
                "skipped": [
                    "‮gnitturc",  # RTL override
                    "note\n  - forged bullet line",  # embedded newline
                    "\x1b[2J\x1b[32mscreen clear",  # ANSI escapes
                    "\x07\x00bell+nul",  # control chars
                    "  legitimate note  ",
                ]
            }
        )
        for item in hostile:
            assert not _UNSAFE_SKIPPED_CHARS.search(item), repr(item)
        # a real note still survives, cleaned
        assert "legitimate note" in hostile

    def test_full_pipeline_surfaces_sanitized_skipped(self) -> None:
        resp = json.dumps(
            {
                "schema_version": "0.5",
                "commands": [
                    {"op": "create_plate", "width": 10, "height": 10, "thickness": 2}
                ],
                "skipped": ["\x1b[31mfake\x1b[0m", "‮evil"],
            }
        )
        out = validate_or_repair("x", resp)
        assert out.ok
        for item in out.skipped:
            assert "\x1b" not in item and "‮" not in item


# --------------------------------------------------------------------------
# MEDIUM/LOW: coming-soon detection precision
# --------------------------------------------------------------------------


class TestDetectionPrecision:
    @pytest.mark.parametrize(
        "text",
        ["a draft drawing of the bracket", "first draft of the design", "rough draft"],
    )
    def test_plain_english_draft_not_flagged(self, text: str) -> None:
        assert detect_unsupported(text) == ()

    def test_real_draft_angle_still_flagged(self) -> None:
        assert {f.key for f in detect_unsupported("2 degree draft angle on the walls")} == {
            "draft"
        }

    @pytest.mark.parametrize(
        ("text", "key"),
        [
            ("try lofting between the profiles", "loft"),
            ("add flanges on both sides", "sheet_metal"),
            ("دودة وترس", "worm_gear"),  # attached Arabic waw
        ],
    )
    def test_previously_missed_phrasings_now_detected(self, text: str, key: str) -> None:
        assert key in {f.key for f in detect_unsupported(text)}

    def test_draft_alternative_names_a_real_field(self) -> None:
        # the coming-soon advice must point at a field that actually exists
        draft = next(f for f in CATALOG if f.key == "draft")
        assert "cut_extrude" in draft.alternative_en
        assert "draft_angle" in CutExtrude.model_fields
        assert "draft_angle" not in Extrude.model_fields

    def test_every_coming_soon_alternative_mentions_a_supported_route(self) -> None:
        for feat in coming_soon():
            assert feat.alternative_en and feat.alternative_ar, feat.key


# --------------------------------------------------------------------------
# LOW: bridge pending-preview cap
# --------------------------------------------------------------------------


class TestPendingCap:
    def test_pending_store_is_bounded(self) -> None:
        state = BridgeState()
        for _ in range(MAX_PENDING + 50):
            state.add_pending(None, [])  # type: ignore[arg-type]
        assert len(state.pending) <= MAX_PENDING

    def test_restore_pending_keeps_a_live_token(self) -> None:
        state = BridgeState()
        tok = state.add_pending(None, [])  # type: ignore[arg-type]
        pending = state.take_pending(tok)
        assert pending is not None
        state.restore_pending(tok, pending)
        assert state.take_pending(tok) is not None  # usable again
