# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""SolidWorks COM backend. Windows-only; requires pywin32 and SolidWorks.

Never import this package at module scope outside of itself — the CLI
imports it lazily so the rest of swpilot stays testable in CI.
"""
