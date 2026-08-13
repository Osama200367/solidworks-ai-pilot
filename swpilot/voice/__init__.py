"""Voice layer (v1.1): speak a part/assembly description instead of typing.

A thin capture + transcription front-end that funnels into the *exact* v1.0
natural-language pipeline (prompt bundle → LLM → validate+repair → confirm →
execute). No new JSON path and no schema change: voice only produces text,
which — after a light dialect normalization — flows through ``swpilot ai``.
"""

from __future__ import annotations

from swpilot.voice.normalize import normalize
from swpilot.voice.transcribe import (
    STTConfig,
    STTConfigError,
    STTRequestError,
    VoiceCaptureError,
    record_to_file,
    transcribe_file,
)

__all__ = [
    "STTConfig",
    "STTConfigError",
    "STTRequestError",
    "VoiceCaptureError",
    "normalize",
    "record_to_file",
    "transcribe_file",
]
