# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Regression tests pinning the confirmed v1.1 adversarial-review findings.

Weighted, as the review was, on the normalizer's cardinal safety property —
never merge two distinct numbers a speaker meant separately — plus the
mic/OS capture surface. Each test fails against the pre-fix code.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from swpilot.voice.normalize import normalize
from swpilot.voice.transcribe import VoiceCaptureError, _encode_multipart, record_to_file


class TestNoBadMergesUnderConjunction:
    def test_english_and_between_distinct_numbers_is_not_summed(self) -> None:
        # finding: `if conj: return True` summed ANY conjoined pair
        assert normalize("twenty and thirty") == "20 and 30"
        assert normalize("five and six holes") == "5 and 6 holes"

    def test_arabic_waw_between_two_tens_is_not_summed(self) -> None:
        # عشرين وثلاثين (twenty and thirty) = two dimensions, never 50
        assert "50" not in normalize("عشرين وثلاثين").split()
        assert "50" not in normalize("ثلاثين وعشرين").split()

    def test_multiplier_times_multiplier_not_fused(self) -> None:
        # مية ومية must not become 10000 (also: مية omitted as it means "water")
        assert normalize("مية ومية") == "مية ومية"

    def test_conjunction_word_is_never_dropped(self) -> None:
        # a consumed-but-not-combined conjunction must survive as prose
        assert normalize("twenty and") == "20 and"
        assert normalize("عشرين و") == "20 و"


class TestArabicTensThenOnesSeparate:
    def test_arabic_tens_then_ones_stays_separate(self) -> None:
        # finding: the English "twenty five"=25 rule wrongly fired on Arabic
        assert normalize("عشرين خمسة") == "20 5"
        assert normalize("خطوة عشرين خمسة") == "خطوة 20 5"

    def test_english_tens_then_ones_still_combines(self) -> None:
        assert normalize("twenty five") == "25"


class TestValidCompoundsStillFold:
    @pytest.mark.parametrize(
        ("spoken", "expected"),
        [
            ("خمسة وعشرين", "25"),  # Arabic ones + tens under conjunction
            ("واحد وعشرين", "21"),
            ("مئة وعشرين", "120"),  # hundred + remainder
            ("one hundred and twenty", "120"),
            ("مئتين وخمسين", "250"),  # 200 + 50 remainder
            ("اثنين عشرة", "12"),  # ones + ten
            ("three hundred", "300"),
        ],
    )
    def test_valid_compounds(self, spoken: str, expected: str) -> None:
        assert normalize(spoken) == expected


class TestTermsNoLongerCorruptCommonWords:
    def test_year_singular_not_rewritten_to_tooth(self) -> None:
        # finding: سنة→سن turned "year" into "tooth"
        assert normalize("القطعة عمرها سنة") == "القطعة عمرها سنة"
        assert normalize("عشرين سنة") == "20 سنة"

    def test_year_plural_not_rewritten_to_teeth(self) -> None:
        assert normalize("منذ سنون") == "منذ سنون"

    def test_water_word_not_rewritten_to_hundred(self) -> None:
        # finding: colloquial مية/ميه = "water" was mapped to 100
        assert normalize("عايز ميه") == "عايز ميه"
        assert normalize("كوباية ميه") == "كوباية ميه"

    def test_thickness_dialect_term_still_maps(self) -> None:
        assert normalize("تخانة عشرة") == "سماكة 10"


class TestCaptureAndMultipartSurface:
    def test_wav_write_failure_raises_voice_capture_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # finding: the WAV-write block sat outside try/except, so a bad path
        # raised a bare OSError that the CLI did not catch. Simulate a working
        # mic so we reach the write, then target a non-existent directory.
        class _FakeSD:
            @staticmethod
            def rec(*a: object, **k: object) -> _Frames:
                return _Frames()

            @staticmethod
            def wait() -> None:
                return None

        class _Frames:
            def tobytes(self) -> bytes:
                return b"\x00\x00"

        monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD)
        with pytest.raises(VoiceCaptureError):
            record_to_file(Path("/no/such/dir/out.wav"), seconds=0.01)

    def test_multipart_escapes_quotes_in_filename(self) -> None:
        # finding: a filename with a double-quote broke the header framing
        body, boundary = _encode_multipart("whisper-1", 'a"b.wav', b"data")
        assert b'filename="a\\"b.wav"' in body

    def test_multipart_strips_crlf_from_filename(self) -> None:
        body, _ = _encode_multipart("whisper-1", "a\r\nb.wav", b"data")
        # no injected header line: the filename's CR/LF became spaces
        assert b"a\r\nb.wav" not in body
        assert b'filename="a  b.wav"' in body

    def test_written_wav_is_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # the happy-path write produces a readable 16-bit mono WAV
        class _FakeSD:
            @staticmethod
            def rec(n: int, **k: object) -> _Frames:
                return _Frames(n)

            @staticmethod
            def wait() -> None:
                return None

        class _Frames:
            def __init__(self, n: int) -> None:
                self._n = n

            def tobytes(self) -> bytes:
                return b"\x00\x00" * self._n

        monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD)
        out = record_to_file(tmp_path / "r.wav", seconds=0.01, samplerate=1000)
        with wave.open(str(out), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 1000
