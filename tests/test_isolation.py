# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Guarantee: mock-backend usage never touches pywin32/COM modules.

This is the CI-enforced isolation boundary — if someone adds a
module-scope win32com import anywhere outside swpilot.backends.solidworks,
these tests fail on any platform.
"""

import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).parent.parent / "examples"

CHECK_SNIPPET = f"""
import json, sys
from swpilot.backends.mock.simulator import MockBackend
from swpilot.commands.loader import load_and_expand
from swpilot.executor import execute
import swpilot.cli  # the CLI module itself must not pull in COM

_, expanded = load_and_expand(r"{(EXAMPLES / 'plate_with_holes.json').as_posix()}")
report = execute(expanded, MockBackend())
assert report.success
banned = [m for m in sys.modules if m.startswith(("win32", "pythoncom", "pywintypes"))]
print(json.dumps(banned))
"""


def test_mock_run_never_imports_com_modules() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK_SNIPPET],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    assert json.loads(result.stdout.strip()) == []


def test_solidworks_package_not_imported_by_package_init() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, swpilot, swpilot.cli, swpilot.executor; "
            "print([m for m in sys.modules if 'solidworks' in m and m.startswith('swpilot')])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
