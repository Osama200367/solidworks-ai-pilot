# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Assemble the model-agnostic prompt bundle and the repair prompt.

The bundle is one plain-text blob — system instructions + the auto-
generated vocabulary + few-shot examples + the user's request — that any
LLM (pasted into a free chat, or sent through the API client) turns into
a CommandFile. No provider-specific formatting.
"""

from __future__ import annotations

from swpilot.llm.examples import few_shots
from swpilot.llm.vocabulary import vocabulary_text

_SYSTEM = """\
You are a translator from a plain-language mechanical-part description into
Sanay3i command JSON. Sanay3i builds parts, assemblies, drawings and gears
in SolidWorks. Your ONLY output is a single JSON object (a "CommandFile") that
conforms exactly to the vocabulary below. Do not explain, do not add prose or
markdown — output only the JSON object.

The request may be in Arabic or English (or a mix); interpret either, but the
JSON keys and enum values are always English. Casual dialect phrasing and
implicit standards are fine — e.g. "standard M8 clearance hole" means a "hole"
command with "standard": "M8".

PARTIAL UNDERSTANDING: if part of the request cannot be expressed with the
vocabulary below, still produce the commands you CAN, and add a top-level
"skipped" array next to "commands" listing briefly (in the requester's
language) what you left out and why. NEVER invent an op that is not in the
vocabulary, and never silently drop part of the request without listing it
in "skipped"."""


def _few_shot_block() -> str:
    parts = []
    for fs in few_shots():
        parts.append(f"REQUEST:\n{fs.request}\n\nJSON:\n{fs.json_text}")
    return "\n\n---\n\n".join(parts)


def build_bundle(request: str) -> str:
    """The full prompt a user pastes into any chat (or the API sends)."""
    return (
        _SYSTEM
        + "\n\n"
        + vocabulary_text()
        + "\n\n"
        + "EXAMPLES:\n\n"
        + _few_shot_block()
        + "\n\n---\n\n"
        + "Now translate THIS request into one CommandFile JSON object "
        + "(JSON only, no prose):\n\nREQUEST:\n"
        + request.strip()
        + "\n\nJSON:"
    )


def build_repair_prompt(request: str, bad_output: str, errors: str) -> str:
    """A follow-up prompt asking the model to fix invalid JSON.

    Used for the automatic API-mode retry and handed to the user verbatim
    in copy-paste mode.
    """
    return (
        "The JSON you produced for the request below did not validate against "
        "the SW-Pilot schema. Fix it and output ONLY the corrected JSON object "
        "(no prose, no markdown).\n\n"
        f"REQUEST:\n{request.strip()}\n\n"
        f"YOUR JSON:\n{bad_output.strip()}\n\n"
        f"VALIDATION ERRORS:\n{errors.strip()}\n\n"
        "Corrected JSON:"
    )
