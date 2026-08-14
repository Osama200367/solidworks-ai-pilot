# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Natural-language layer (v1.0): plain-language → CommandFile JSON.

This package produces a model-agnostic prompt bundle (schema vocabulary +
few-shot examples + the user's request) that ANY LLM can turn into a
CommandFile, and a strict extract → validate → one-repair loop around the
result. It never touches COM: it only produces JSON that the existing,
already-hardened engine (v0.1–v0.5) validates, expands, and executes.
"""

from __future__ import annotations

from swpilot.llm.extract import ExtractionError, extract_json
from swpilot.llm.prompt import build_bundle, build_repair_prompt
from swpilot.llm.repair import RepairOutcome, validate_or_repair
from swpilot.llm.vocabulary import vocabulary_text

__all__ = [
    "ExtractionError",
    "RepairOutcome",
    "build_bundle",
    "build_repair_prompt",
    "extract_json",
    "validate_or_repair",
    "vocabulary_text",
]
