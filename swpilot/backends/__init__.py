# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Execution backends.

``swpilot.backends.mock`` runs anywhere and is what CI tests against.
``swpilot.backends.solidworks`` requires Windows + pywin32 + SolidWorks
and must only ever be imported lazily (see ``cli.py``).
"""
