# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Shared geometric model layer (the "digital twin").

Pure Python, no COM. The executor maintains a :class:`ModelTracker` for
every backend: it validates commands, derives 3D faces/edges for the
solids being built, and resolves declarative selectors into concrete
pick coordinates — with identical results whether the backend is the
mock logger or real SolidWorks.
"""
