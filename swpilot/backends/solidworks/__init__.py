"""SolidWorks COM backend. Windows-only; requires pywin32 and SolidWorks.

Never import this package at module scope outside of itself — the CLI
imports it lazily so the rest of swpilot stays testable in CI.
"""
