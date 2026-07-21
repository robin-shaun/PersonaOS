from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_start_script_is_executable_and_has_valid_shell_syntax() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "start.sh"

    assert os.access(script, os.X_OK)
    syntax = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_start_script_help_does_not_start_services() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(root / "start.sh"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert "./start.sh [--install]" in result.stdout
    assert "Ctrl+C" in result.stdout
