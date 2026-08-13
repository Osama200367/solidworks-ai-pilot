# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Few-shot examples for the prompt bundle.

Each pair is a natural-language request and the CommandFile JSON that
answers it. The JSON is loaded from a real, CI-validated example file so
the few-shots are provably correct and can never drift from the schema.
One request is in Arabic (the gear — the acceptance case) to demonstrate
the Arabic-request → English-JSON mapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


@dataclass(frozen=True)
class FewShot:
    request: str
    json_text: str  # canonical JSON of the CommandFile


def _load(name: str) -> str:
    data = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    return json.dumps(data, ensure_ascii=False, indent=2)


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
    ]
