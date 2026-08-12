"""Execution backends.

``swpilot.backends.mock`` runs anywhere and is what CI tests against.
``swpilot.backends.solidworks`` requires Windows + pywin32 + SolidWorks
and must only ever be imported lazily (see ``cli.py``).
"""
