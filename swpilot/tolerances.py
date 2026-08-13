# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Shared geometric tolerance.

1e-6 mm = 1 nm: far below manufacturing relevance, far above float noise
at the coordinate magnitudes we deal with (~1e2 mm). Lives in a neutral
module so both the command layer (macro validation) and the mock backend
(geometry predicates) apply the identical tolerance — a macro-accepted
file must never fail the simulator's equivalent check.
"""

EPS = 1e-6
