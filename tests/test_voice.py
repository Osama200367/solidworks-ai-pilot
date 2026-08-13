"""Tests for the voice layer (v1.1): transcription, capture, and the pipeline.

Transcription is proven with a recorded (mocked) Whisper response — we never
call a live STT service or decode real audio. The acceptance path shows a
spoken Arabic gear request flowing transcript → normalize → (recorded LLM) →
mock, running 1/1. Microphone capture is only degradation-tested (no hardware
in CI).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from swpilot.backends.mock.simulator import MockBackend
from swpilot.cli import app
from swpilot.commands.loader import expand_commands
from swpilot.executor import execute
from swpilot.llm import validate_or_repair
from swpilot.voice import normalize
from swpilot.voice.transcribe import (
    STTConfig,
    STTConfigError,
    STTRequestError,
    VoiceCaptureError,
    record_to_file,
    transcribe_file,
)

runner = CliRunner()


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _Resp(json.dumps(payload).encode("utf-8")),
    )


# --------------------------------------------------------------------------
# STT config
# --------------------------------------------------------------------------


class TestSTTConfig:
    def test_no_key_and_default_base_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SWPILOT_STT_API_KEY", raising=False)
        monkeypatch.delenv("SWPILOT_STT_BASE_URL", raising=False)
        with pytest.raises(STTConfigError):
            STTConfig.from_env()

    def test_key_enables_api_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWPILOT_STT_API_KEY", "sk-test")
        cfg = STTConfig.from_env()
        assert cfg.api_key == "sk-test"
        assert cfg.model == "whisper-1"  # default

    def test_local_server_needs_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SWPILOT_STT_API_KEY", raising=False)
        monkeypatch.setenv("SWPILOT_STT_BASE_URL", "http://localhost:9000/v1")
        cfg = STTConfig.from_env()  # non-default base ⇒ allowed without a key
        assert cfg.base_url == "http://localhost:9000/v1"


# --------------------------------------------------------------------------
# Transcription (recorded response)
# --------------------------------------------------------------------------


class TestTranscribeFile:
    def _cfg(self) -> STTConfig:
        return STTConfig(base_url="http://localhost/v1", model="whisper-1", api_key="")

    def test_returns_text(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF....WAVEfake")
        _patch_urlopen(monkeypatch, {"text": "  a plate one hundred by fifty  "})
        assert transcribe_file(audio, self._cfg()) == "a plate one hundred by fifty"

    def test_empty_audio_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "e.wav"
        empty.write_bytes(b"")
        with pytest.raises(STTRequestError):
            transcribe_file(empty, self._cfg())

    def test_missing_text_field_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake")
        _patch_urlopen(monkeypatch, {"error": "bad"})
        with pytest.raises(STTRequestError):
            transcribe_file(audio, self._cfg())

    def test_blank_text_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake")
        _patch_urlopen(monkeypatch, {"text": "   "})
        with pytest.raises(STTRequestError):
            transcribe_file(audio, self._cfg())


# --------------------------------------------------------------------------
# Microphone capture (degradation only — no hardware in CI)
# --------------------------------------------------------------------------


class TestMicCapture:
    def test_missing_library_degrades_cleanly(self, tmp_path: Path) -> None:
        # sounddevice is not installed in CI; must raise VoiceCaptureError,
        # never a bare ImportError.
        with pytest.raises(VoiceCaptureError):
            record_to_file(tmp_path / "out.wav", seconds=0.1)

    def test_nonpositive_duration_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(VoiceCaptureError):
            record_to_file(tmp_path / "out.wav", seconds=0)


# --------------------------------------------------------------------------
# Acceptance: spoken Arabic gear → transcript → normalize → LLM → mock
# --------------------------------------------------------------------------


class TestVoiceAcceptance:
    def test_spoken_arabic_gear_runs_1_of_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 1. recorded STT response for the casually-spoken request
        spoken = "بدي ترس موديول اثنين عشرين سن مع تجويف ستاشر وخابور"
        audio = tmp_path / "gear.wav"
        audio.write_bytes(b"fake-audio")
        _patch_urlopen(monkeypatch, {"text": spoken})
        cfg = STTConfig(base_url="http://localhost/v1", model="whisper-1", api_key="")
        transcript = transcribe_file(audio, cfg)

        # 2. normalization: spoken numbers → digits, kept distinct
        normalized = normalize(transcript)
        assert "2" in normalized.split()  # module
        assert "20" in normalized.split()  # teeth
        assert "16" in normalized.split()  # bore
        assert "22" not in normalized.split()  # module & teeth never merged

        # 3. the exact v1.0 path with a recorded LLM response
        llm_response = json.dumps(
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
        out = validate_or_repair(normalized, llm_response)
        assert out.ok
        assert out.command_file.commands[0].op == "involute_spur_gear"  # type: ignore[union-attr]
        expanded = expand_commands(list(out.command_file.commands))  # type: ignore[union-attr]
        report = execute(expanded, MockBackend())
        assert report.success


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


class TestVoiceCLI:
    def test_text_copy_paste_emits_normalized_bundle(self) -> None:
        result = runner.invoke(app, ["voice", "--text", "ترس عشرين سن", "--mode", "copy-paste"])
        assert result.exit_code == 0
        assert "COMMAND VOCABULARY" in result.output
        # normalized request (20) is echoed for the user to see
        assert "20" in result.stderr or "20" in result.output

    def test_offline_handoff_without_stt_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SWPILOT_STT_API_KEY", raising=False)
        monkeypatch.delenv("SWPILOT_STT_BASE_URL", raising=False)
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"fake")
        result = runner.invoke(app, ["voice", str(audio)])
        # offline handoff prints instructions and executes nothing
        assert result.exit_code == 0
        assert "voice --text" in result.output
        assert not (tmp_path / "ai_run.report.json").exists()

    def test_empty_transcript_rejected(self) -> None:
        result = runner.invoke(app, ["voice", "--text", "   "])
        assert result.exit_code == 2

    def test_mic_unavailable_reports_cleanly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # no audio, no --text ⇒ tries the mic, which is absent in CI
        monkeypatch.delenv("SWPILOT_STT_API_KEY", raising=False)
        result = runner.invoke(app, ["voice", "--record-seconds", "0.1"])
        assert result.exit_code == 1
        assert "microphone" in result.output.lower()
