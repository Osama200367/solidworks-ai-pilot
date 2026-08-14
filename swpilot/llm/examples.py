# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Few-shot examples for the prompt bundle.

Each pair is a natural-language request and the CommandFile JSON that
answers it. File-based examples are loaded from real, CI-validated example
files; inline examples are validated against the live schema at build time
— either way the few-shots are provably correct and can never drift.
Coverage: English, Arabic dialect (the gear acceptance case), a compound
mixed-language request with an implicit standard, and an out-of-scope
request demonstrating the "skipped" envelope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from swpilot.commands.loader import parse_command_data

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


@dataclass(frozen=True)
class FewShot:
    request: str
    json_text: str  # canonical JSON of the CommandFile (+ optional "skipped")


def _load(name: str) -> str:
    data = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    return json.dumps(data, ensure_ascii=False, indent=2)


def _inline(data: dict[str, object]) -> str:
    """Render an inline example, validating its commands against the schema."""
    check = dict(data)
    check.pop("skipped", None)  # the envelope field is not part of the schema
    parse_command_data(check)  # raises if the example ever drifts
    return json.dumps(data, ensure_ascii=False, indent=2)


# Compound + mixed Arabic/English + implicit metric standard in one sentence.
_COMPOUND_MIXED: dict[str, object] = {
    "schema_version": "0.5",
    "commands": [
        {"op": "create_plate", "width": 120, "height": 80, "thickness": 12},
        {"op": "fillet", "radius": 8, "edges": {"select": "vertical_corners"}},
        {
            "op": "hole",
            "type": "counterbore",
            "standard": "M8",
            "at": [[-45, -25], [45, -25], [-45, 25], [45, 25]],
        },
        {"op": "save_part", "path": "base_plate.SLDPRT"},
    ],
}

# Out-of-scope handling: build what is supported, declare the rest in
# "skipped" — never invent an op.
_PARTIAL_SKIPPED: dict[str, object] = {
    "schema_version": "0.5",
    "commands": [
        {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
        {"op": "save_part", "path": "bracket.SLDPRT"},
    ],
    "skipped": [
        "الثني (sheet metal bend) غير متوفر بالنسخة الحالية — أنجزت اللوح المسطح فقط"
    ],
}


def few_shots() -> list[FewShot]:
    return [
        FewShot(
            request="a 100 by 50 by 10 mm plate with four 8 mm holes in the corners",
            json_text=_load("plate_with_holes.json"),
        ),
        FewShot(
            request=(
                "an assembly: a 120x80x12 base plate and an 8 mm cover, bolted "
                "together with four M8 socket-head cap screws"
            ),
            json_text=_load("bolted_cover.json"),
        ),
        FewShot(
            # Arabic: "I want an m2 gear with 20 teeth, a 16 bore and a keyway"
            request="بدي ترس module 2 بـ20 سن مع تجويف 16 وخابور",
            json_text=_load("spur_gear_m2_z20.json"),
        ),
        FewShot(
            # compound + mixed-language + implicit standard, one sentence
            request=(
                "قاعدة aluminum مقاس 120 في 80 سماكة 12، rounded corners نصف "
                "قطرها 8، مع أربع standard M8 counterbore holes بالزوايا"
            ),
            json_text=_inline(_COMPOUND_MIXED),
        ),
        FewShot(
            # out-of-scope part of a request → commands + "skipped", no invention
            request="بدي bracket صفيحة 100 في 50 سماكة 10 مع ثني sheet metal على الحافة",
            json_text=_inline(_PARTIAL_SKIPPED),
        ),
    ]
