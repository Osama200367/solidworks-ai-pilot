# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Extract → validate → (one) repair loop around an LLM response.

Turns a raw LLM response into a validated ``CommandFile`` or, on failure,
a ready-to-use repair prompt plus the exact validation errors. The same
path serves both modes: API mode passes a ``retry`` callback that calls
the model again automatically; copy-paste mode passes none and hands the
repair prompt to the user.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from swpilot.commands.loader import CommandFileError, parse_command_data
from swpilot.commands.schema import CommandFile
from swpilot.llm.extract import ExtractionError, extract_json
from swpilot.llm.prompt import build_repair_prompt

# Bounds on the model-supplied "skipped" list (untrusted): enough to describe
# real omissions, small enough that a hostile response can't bloat output.
_MAX_SKIPPED_ITEMS = 20
_MAX_SKIPPED_LEN = 300
# C0/C1 controls (incl. ESC and newlines) and Unicode bidi override/isolate
# chars: this text is printed into the CLI confirmation prompt, so ANSI escapes,
# forged newlines, and RTL overrides must not survive.
_UNSAFE_SKIPPED_CHARS = re.compile(
    "[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]"
)


@dataclass
class RepairOutcome:
    """The result of validating an LLM response (after up to one repair)."""

    command_file: CommandFile | None  # None if still invalid
    errors: str | None = None  # formatted validation/extraction errors
    repair_prompt: str | None = None  # paste-back prompt when unresolved
    repaired: bool = False  # a repair pass was used and succeeded
    skipped: list[str] = field(default_factory=list)  # model-declared omissions

    @property
    def ok(self) -> bool:
        return self.command_file is not None


def _take_skipped(data: dict[str, object]) -> list[str]:
    """Pop the optional envelope-level "skipped" list (model-declared omissions).

    Removed BEFORE CommandFile validation so the engine schema stays
    ``extra="forbid"``; sanitized because it is model-authored text that will
    be shown to the user.
    """
    raw = data.pop("skipped", None)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:_MAX_SKIPPED_ITEMS]:
        if not isinstance(item, str):
            continue
        # Neutralize ANSI/control/bidi before this reaches the CLI prompt.
        text = _UNSAFE_SKIPPED_CHARS.sub(" ", item).strip()[:_MAX_SKIPPED_LEN]
        if text:
            out.append(text)
    return out


def _validate(response: str) -> tuple[CommandFile | None, str | None, list[str]]:
    """(command_file, None, skipped) on success; (None, error, []) on failure."""
    try:
        data = extract_json(response)
    except ExtractionError as exc:
        return None, str(exc), []
    skipped = _take_skipped(data)
    try:
        return parse_command_data(data), None, skipped
    except CommandFileError as exc:
        return None, str(exc), []


def validate_or_repair(
    request: str,
    response: str,
    retry: Callable[[str], str] | None = None,
) -> RepairOutcome:
    """Validate an LLM response; attempt exactly one repair if it fails.

    ``retry`` (API mode) is called with the repair prompt and must return
    the model's second response; without it (copy-paste mode) the outcome
    carries the repair prompt for the user to run manually.
    """
    cf, errors, skipped = _validate(response)
    if cf is not None:
        return RepairOutcome(command_file=cf, skipped=skipped)

    repair_prompt = build_repair_prompt(request, response, errors or "")
    if retry is None:
        return RepairOutcome(
            command_file=None, errors=errors, repair_prompt=repair_prompt
        )

    # one automatic repair attempt
    second = retry(repair_prompt)
    cf2, errors2, skipped2 = _validate(second)
    if cf2 is not None:
        return RepairOutcome(command_file=cf2, repaired=True, skipped=skipped2)
    # still invalid after one repair — surface the second round's errors
    return RepairOutcome(
        command_file=None,
        errors=errors2,
        repair_prompt=build_repair_prompt(request, second, errors2 or ""),
        repaired=True,
    )
