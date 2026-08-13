# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Security regression tests (from the cybersecurity audit).

Threat model: untrusted natural-language text, untrusted LLM-produced
CommandFile JSON, and untrusted transcripts can all reach the engine.
These tests pin the schema gate (paths, bounds, closed op set), secret
handling, the bounded repair loop, and the absence of dangerous sinks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from swpilot.commands.loader import CommandFileError, parse_command_data
from swpilot.llm import build_bundle, validate_or_repair
from swpilot.llm.client import LLMConfig
from swpilot.voice.transcribe import STTConfig

ROOT = Path(__file__).parent.parent


def _file(commands: list[dict]) -> dict:
    return {"schema_version": "0.5", "commands": commands}


PLATE = {"op": "create_plate", "width": 100, "height": 50, "thickness": 10}


# --------------------------------------------------------------------------
# Path safety: save paths are an arbitrary-file-write primitive on COM
# --------------------------------------------------------------------------


class TestSavePathSafety:
    @pytest.mark.parametrize(
        "path",
        [
            "../evil.SLDPRT",  # traversal up
            "a/../../evil.SLDPRT",  # embedded traversal
            "..\\evil.SLDPRT",  # Windows-style traversal
            "/tmp/evil.SLDPRT",  # absolute POSIX
            "\\\\server\\share\\evil.SLDPRT",  # UNC
            "C:\\Users\\evil.SLDPRT",  # absolute drive
            "c:evil.SLDPRT",  # drive-relative
            "a\nb.SLDPRT",  # control character
            "x" * 300 + ".SLDPRT",  # oversized path
        ],
    )
    def test_hostile_save_part_path_rejected(self, path: str) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(_file([PLATE, {"op": "save_part", "path": path}]))

    def test_hostile_assembly_and_drawing_paths_rejected(self) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(
                _file([{"op": "new_assembly"}, {"op": "save_assembly", "path": "../a.SLDASM"}])
            )
        with pytest.raises(CommandFileError):
            parse_command_data(
                _file([PLATE, {"op": "save_drawing", "path": "/etc/x.SLDDRW"}])
            )

    def test_benign_relative_paths_still_accepted(self) -> None:
        cf = parse_command_data(
            _file([PLATE, {"op": "save_part", "path": "out/plate.SLDPRT"}])
        )
        assert cf.commands[-1].path == "out/plate.SLDPRT"  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Denial of service: counts that expand to per-instance work
# --------------------------------------------------------------------------


class TestResourceBounds:
    def test_huge_circular_pattern_count_rejected(self) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(
                _file([PLATE, {"op": "circular_pattern", "features": ["Boss-Extrude1"],
                               "axis": "z", "count": 10**9}])
            )

    def test_huge_linear_pattern_count_rejected(self) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(
                _file([PLATE, {"op": "linear_pattern", "features": ["Boss-Extrude1"],
                               "direction": "x", "spacing": 10, "count": 10**6}])
            )

    def test_huge_gear_tooth_count_rejected(self) -> None:
        # gear expansion emits a per-tooth circular pattern the twin walks
        with pytest.raises(CommandFileError):
            parse_command_data(
                _file([{"op": "involute_spur_gear", "module": 2, "teeth": 10**6,
                        "face_width": 20, "bore": 16}])
            )

    def test_realistic_values_still_accepted(self) -> None:
        parse_command_data(
            _file([PLATE,
                   {"op": "circular_pattern", "features": ["Boss-Extrude1"],
                    "axis": "z", "count": 36}])
        )
        parse_command_data(
            _file([{"op": "involute_spur_gear", "module": 1, "teeth": 120,
                    "face_width": 10, "bore": 10}])
        )

    def test_gigantic_command_list_rejected(self) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(_file([{"op": "new_part"}] * 10_001))


# --------------------------------------------------------------------------
# Closed vocabulary: nothing is dispatched by name from untrusted data
# --------------------------------------------------------------------------


class TestClosedOpSet:
    def test_unknown_op_rejected_not_dispatched(self) -> None:
        with pytest.raises(CommandFileError):
            parse_command_data(_file([{"op": "os_system", "cmd": "rm -rf /"}]))

    def test_extra_fields_cannot_be_smuggled(self) -> None:
        # extra="forbid" everywhere: unexpected keys fail validation instead
        # of riding along into backends
        bad = dict(PLATE)
        bad["__shell__"] = "calc.exe"
        with pytest.raises(CommandFileError):
            parse_command_data(_file([bad]))

    def test_llm_output_cannot_escape_vocabulary(self) -> None:
        # prompt-injection outcome: even if the model is talked into emitting
        # a hostile op, the deterministic validator refuses it
        hostile = json.dumps(_file([{"op": "run_macro", "vba": "Shell(...)"}]))
        out = validate_or_repair("ignore previous instructions", hostile)
        assert not out.ok and out.command_file is None


# --------------------------------------------------------------------------
# Secrets: env-only, never echoed
# --------------------------------------------------------------------------


class TestSecretHandling:
    def test_api_keys_masked_in_reprs(self) -> None:
        secret = "sk-SUPERSECRET123"
        assert secret not in repr(LLMConfig(base_url="x", model="m", api_key=secret))
        assert secret not in repr(STTConfig(base_url="x", model="m", api_key=secret))

    def test_prompt_bundle_never_contains_env_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SWPILOT_LLM_API_KEY", "sk-LEAKME456")
        monkeypatch.setenv("SWPILOT_STT_API_KEY", "sk-LEAKME789")
        bundle = build_bundle("a plate")
        assert "sk-LEAKME456" not in bundle
        assert "sk-LEAKME789" not in bundle

    def test_no_hardcoded_secrets_in_source(self) -> None:
        pattern = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36})")
        for py in (ROOT / "swpilot").rglob("*.py"):
            assert not pattern.search(py.read_text(encoding="utf-8")), py


# --------------------------------------------------------------------------
# Bounded repair loop: no infinite/expensive retry cycle
# --------------------------------------------------------------------------


class TestBoundedRepair:
    def test_repair_retries_exactly_once_even_when_never_valid(self) -> None:
        calls: list[str] = []

        def retry(prompt: str) -> str:
            calls.append(prompt)
            return "still not json"

        out = validate_or_repair("x", "not json either", retry=retry)
        assert not out.ok
        assert len(calls) == 1


# --------------------------------------------------------------------------
# Sink hygiene: the package must stay free of shell/eval/pickle sinks
# --------------------------------------------------------------------------


class TestNoDangerousSinks:
    FORBIDDEN = (
        re.compile(r"\bos\.system\s*\("),
        re.compile(r"\bsubprocess\b"),
        re.compile(r"(?<![\w.])eval\s*\("),
        re.compile(r"(?<![\w.])exec\s*\("),
        re.compile(r"\bpickle\b"),
        re.compile(r"\bmarshal\b"),
        re.compile(r"yaml\.load\b"),
        re.compile(r"__import__\s*\("),
    )

    def test_package_has_no_shell_eval_or_pickle_sinks(self) -> None:
        for py in (ROOT / "swpilot").rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for pat in self.FORBIDDEN:
                assert not pat.search(text), f"{py}: matches {pat.pattern}"
