# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Metric fastener hole presets.

Convenience defaults for ``hole`` commands carrying ``"standard": "M6"``
etc. Values are nominal, drawn from common ISO hole charts:

* clearance diameter — ISO 273 medium fit
* counterbore — for hex socket head cap screws (ISO 4762): bore diameter
  with working clearance, depth = nominal head height (flush seating)
* countersink — for hex socket flat head screws (ISO 10642): head
  diameter at 90° included angle

They are conveniences, not certified standard data: verify against the
fastener specification for anything that matters, and override any
field explicitly in the command to win over the preset.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FastenerPreset:
    clearance_diameter: float  # mm
    cb_diameter: float  # mm
    cb_depth: float  # mm
    cs_diameter: float  # mm
    cs_angle: float = 90.0  # included angle, degrees


FASTENER_PRESETS: dict[str, FastenerPreset] = {
    "M3": FastenerPreset(3.4, 6.5, 3.0, 6.3),
    "M4": FastenerPreset(4.5, 8.0, 4.0, 8.4),
    "M5": FastenerPreset(5.5, 10.0, 5.0, 10.4),
    "M6": FastenerPreset(6.6, 11.0, 6.0, 12.6),
    "M8": FastenerPreset(9.0, 15.0, 8.0, 17.3),
    "M10": FastenerPreset(11.0, 18.0, 10.0, 20.0),
    "M12": FastenerPreset(13.5, 20.0, 12.0, 24.4),
}


def preset_names() -> list[str]:
    return sorted(FASTENER_PRESETS, key=lambda k: float(k[1:]))
