"""Defensive JSON extraction from an LLM response.

Free models wrap JSON in prose, ```json fences, or leading commentary.
This pulls the CommandFile object out robustly: prefer a fenced block,
else the outermost balanced ``{...}``. It only *locates and parses* JSON —
schema validation is a separate step (``repair.py``).
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ExtractionError(ValueError):
    """No parseable JSON object could be found in the response."""


def _balanced_objects(text: str) -> list[str]:
    """Every top-level balanced {...} span, respecting strings/escapes."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start : i + 1])
                start = -1
    return spans


def _try_load(candidate: str) -> dict[str, object] | None:
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_json(response: str) -> dict[str, object]:
    """Return the CommandFile-shaped dict from an LLM response.

    Tries, in order: fenced code blocks (last one wins — models often
    restate the answer last), then the largest balanced brace span that
    parses AND looks like a CommandFile (has a "commands" key), then any
    parseable object. Raises :class:`ExtractionError` if nothing parses.
    """
    if not response or not response.strip():
        raise ExtractionError("empty response")

    candidates: list[str] = []
    # fenced blocks first (reversed: a model's final restatement wins)
    for block in reversed(_FENCE_RE.findall(response)):
        candidates.append(block.strip())
    # then balanced brace spans, largest first (the whole object over a nested one)
    candidates.extend(sorted(_balanced_objects(response), key=len, reverse=True))

    parsed = [obj for c in candidates if (obj := _try_load(c)) is not None]
    if not parsed:
        raise ExtractionError(
            "no JSON object found in the response; the model may have replied "
            "with prose only — try again or use a different model"
        )
    # prefer an object that looks like a CommandFile
    for obj in parsed:
        if "commands" in obj:
            return obj
    return parsed[0]
