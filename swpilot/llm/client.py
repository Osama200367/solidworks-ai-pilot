# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Optional API mode: one OpenAI-compatible chat client.

A single provider-agnostic client hitting the OpenAI ``/chat/completions``
shape with a configurable base URL, model and key. That one shape already
covers OpenAI, OpenRouter, Groq, Together, and local Ollama / LM Studio,
so API mode works with most free endpoints without per-provider code.
Copy-paste mode needs none of this.

Configuration (environment):
* ``SWPILOT_LLM_BASE_URL`` — e.g. https://api.openai.com/v1 (default),
  https://openrouter.ai/api/v1, http://localhost:11434/v1 (Ollama)
* ``SWPILOT_LLM_MODEL`` — the model id (required for API mode)
* ``SWPILOT_LLM_API_KEY`` — the key (blank is allowed for local servers)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class LLMConfigError(RuntimeError):
    """API mode is not configured (no model / key); use copy-paste mode."""


class LLMRequestError(RuntimeError):
    """The LLM endpoint returned an error or an unreadable response."""


@dataclass
class LLMConfig:
    base_url: str
    model: str
    # repr=False: the key must never leak into logs/tracebacks via repr().
    api_key: str = field(repr=False)
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        model = os.environ.get("SWPILOT_LLM_MODEL", "").strip()
        if not model:
            raise LLMConfigError(
                "API mode needs SWPILOT_LLM_MODEL (and usually "
                "SWPILOT_LLM_API_KEY); set them or use --mode copy-paste"
            )
        return cls(
            base_url=os.environ.get("SWPILOT_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=model,
            api_key=os.environ.get("SWPILOT_LLM_API_KEY", ""),
        )


class OpenAICompatibleClient:
    """Minimal chat client over the OpenAI /chat/completions shape."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def complete(self, prompt: str) -> str:
        """Send a single user prompt, return the assistant's text."""
        body = json.dumps(
            {
                "model": self._config.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        req = urllib.request.Request(
            f"{self._config.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMRequestError(
                f"LLM endpoint returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMRequestError(f"could not reach the LLM endpoint: {exc}") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMRequestError(f"unreadable response from the LLM endpoint: {exc}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError(
                f"unexpected response shape from the LLM endpoint: {repr(payload)[:300]}"
            ) from exc
        if content is None:
            # A null content (e.g. finish_reason=length or content_filter)
            # would str() into the literal "None" and be fed to the parser.
            raise LLMRequestError(
                "the LLM endpoint returned empty content (null); the completion "
                "was likely truncated or filtered — check finish_reason"
            )
        return str(content)
