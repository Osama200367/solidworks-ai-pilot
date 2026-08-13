# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Speech-to-text: an optional Whisper-compatible client + a thin mic wrapper.

Transcription mirrors v1.0's provider-agnostic philosophy. API mode POSTs the
audio to an OpenAI-compatible ``/audio/transcriptions`` endpoint (OpenAI,
Groq, a local whisper server, …). Copy-paste/offline mode needs none of this:
the CLI saves the recording and the user transcribes it with any free tool.

Microphone capture is deliberately thin and import-guarded: the heavy audio
dependency (``sounddevice``) is optional and absent in headless CI, so a
missing mic or library raises a clear :class:`VoiceCaptureError` instead of an
import crash. Only the file path is exercisable without hardware.

Configuration (environment), independent of the chat LLM so the two endpoints
may differ:
* ``SWPILOT_STT_BASE_URL`` — e.g. https://api.openai.com/v1 (default)
* ``SWPILOT_STT_MODEL`` — model id (default "whisper-1")
* ``SWPILOT_STT_API_KEY`` — the key (blank allowed for local servers)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_STT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_STT_MODEL = "whisper-1"


class STTConfigError(RuntimeError):
    """API-mode transcription is not configured; use offline mode."""


class STTRequestError(RuntimeError):
    """The transcription endpoint errored or returned an unreadable body."""


class VoiceCaptureError(RuntimeError):
    """Microphone capture is unavailable (no library / no device / failed)."""


@dataclass
class STTConfig:
    base_url: str
    model: str
    # repr=False: the key must never leak into logs/tracebacks via repr().
    api_key: str = field(repr=False)
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> STTConfig:
        key = os.environ.get("SWPILOT_STT_API_KEY", "")
        base = os.environ.get("SWPILOT_STT_BASE_URL", DEFAULT_STT_BASE_URL).rstrip("/")
        # A key (or an explicit non-default base URL, e.g. a local server) marks
        # API mode as intended. Otherwise transcription must go through offline
        # mode — never silently hit the paid default.
        if not key and base == DEFAULT_STT_BASE_URL:
            raise STTConfigError(
                "API transcription needs SWPILOT_STT_API_KEY (and optionally "
                "SWPILOT_STT_BASE_URL / SWPILOT_STT_MODEL); without it, use "
                "offline mode: transcribe the saved audio with any free tool "
                "and pass the text via `swpilot voice --text`"
            )
        return cls(
            base_url=base,
            model=os.environ.get("SWPILOT_STT_MODEL", DEFAULT_STT_MODEL).strip()
            or DEFAULT_STT_MODEL,
            api_key=key,
        )


def _encode_multipart(model: str, filename: str, audio: bytes) -> tuple[bytes, str]:
    """Build a minimal multipart/form-data body (model + file). No deps."""
    boundary = "----swpilot" + uuid.uuid4().hex
    crlf = b"\r\n"
    dash = b"--" + boundary.encode("ascii")
    # Escape quotes/backslashes and strip CR/LF so an odd filename can't break
    # the header framing or inject extra parts (RFC 7578 §5.1).
    safe_name = (
        filename.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    )
    lines: list[bytes] = [
        dash,
        b'Content-Disposition: form-data; name="model"',
        b"",
        model.encode("utf-8"),
        dash,
        (
            b'Content-Disposition: form-data; name="file"; filename="'
            + safe_name.encode("utf-8")
            + b'"'
        ),
        b"Content-Type: application/octet-stream",
        b"",
        audio,
        dash + b"--",
        b"",
    ]
    return crlf.join(lines), boundary


def transcribe_file(path: str | Path, config: STTConfig) -> str:
    """Transcribe an audio file via the configured Whisper-compatible endpoint."""
    p = Path(path)
    try:
        audio = p.read_bytes()
    except OSError as exc:
        raise STTRequestError(f"cannot read audio file {p}: {exc}") from exc
    if not audio:
        raise STTRequestError(f"audio file {p} is empty")

    body, boundary = _encode_multipart(config.model, p.name, audio)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    req = urllib.request.Request(
        f"{config.base_url}/audio/transcriptions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise STTRequestError(
            f"transcription endpoint returned HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise STTRequestError(f"could not reach the transcription endpoint: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise STTRequestError(f"unreadable transcription response: {exc}") from exc

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise STTRequestError(
            f"transcription response had no usable text: {repr(payload)[:300]}"
        )
    return text.strip()


def record_to_file(
    path: str | Path,
    seconds: float = 8.0,
    samplerate: int = 16000,
) -> Path:
    """Record ``seconds`` of mono audio from the default mic to a WAV file.

    Thin and import-guarded: requires the optional ``sounddevice`` library and
    a real input device, so it raises :class:`VoiceCaptureError` (never an
    ImportError) where those are absent — e.g. headless CI. Verified only on
    real hardware.
    """
    if seconds <= 0:
        raise VoiceCaptureError("recording length must be positive")
    try:
        import sounddevice  # type: ignore[import-not-found]
    except Exception as exc:  # ImportError, or PortAudio load failure
        raise VoiceCaptureError(
            "microphone capture needs the optional 'sounddevice' library "
            "(pip install 'swpilot[voice]') and an available input device; "
            "on a headless machine, record with any tool and pass the file path"
        ) from exc

    import wave

    try:
        frames = sounddevice.rec(
            int(seconds * samplerate), samplerate=samplerate, channels=1, dtype="int16"
        )
        sounddevice.wait()
    except Exception as exc:  # no device, PortAudio error, etc.
        raise VoiceCaptureError(f"microphone capture failed: {exc}") from exc

    out = Path(path)
    # Open the file ourselves so a bad path/permission fails here cleanly,
    # rather than half-constructing a wave.Wave_write inside wave.open().
    try:
        handle = out.open("wb")
    except OSError as exc:  # missing dir, permission, etc.
        raise VoiceCaptureError(f"could not save recording to {out}: {exc}") from exc
    try:
        with wave.open(handle, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # int16
            wav.setframerate(samplerate)
            wav.writeframes(frames.tobytes())
    except OSError as exc:  # disk full mid-write, etc.
        raise VoiceCaptureError(f"could not save recording to {out}: {exc}") from exc
    finally:
        handle.close()
    return out
