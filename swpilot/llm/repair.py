# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Extract → validate → (one) repair loop around an LLM response.

Turns a raw LLM response into a validated ``CommandFile`` or, on failure,
a ready-to-use repair prompt plus the exact validation errors. The same
path serves both modes: API mode passes a ``retry`` callback that calls
the model again automatically; copy-paste mode passes none and hands the
repair prompt to the user.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from swpilot.commands.loader import CommandFileError, parse_command_data
from swpilot.commands.schema import CommandFile
from swpilot.llm.extract import ExtractionError, extract_json
from swpilot.llm.prompt import build_repair_prompt


@dataclass
class RepairOutcome:
    """The result of validating an LLM response (after up to one repair)."""

    command_file: CommandFile | None  # None if still invalid
    errors: str | None = None  # formatted validation/extraction errors
    repair_prompt: str | None = None  # paste-back prompt when unresolved
    repaired: bool = False  # a repair pass was used and succeeded

    @property
    def ok(self) -> bool:
        return self.command_file is not None


def _validate(response: str) -> tuple[CommandFile | None, str | None]:
    """(command_file, None) on success; (None, error_text) on failure."""
    try:
        data = extract_json(response)
    except ExtractionError as exc:
        return None, str(exc)
    try:
        return parse_command_data(data), None
    except CommandFileError as exc:
        return None, str(exc)


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
    cf, errors = _validate(response)
    if cf is not None:
        return RepairOutcome(command_file=cf)

    repair_prompt = build_repair_prompt(request, response, errors or "")
    if retry is None:
        return RepairOutcome(
            command_file=None, errors=errors, repair_prompt=repair_prompt
        )

    # one automatic repair attempt
    second = retry(repair_prompt)
    cf2, errors2 = _validate(second)
    if cf2 is not None:
        return RepairOutcome(command_file=cf2, repaired=True)
    # still invalid after one repair — surface the second round's errors
    return RepairOutcome(
        command_file=None,
        errors=errors2,
        repair_prompt=build_repair_prompt(request, second, errors2 or ""),
        repaired=True,
    )
