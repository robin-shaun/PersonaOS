from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_passes_for_committed_artifacts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/release_check.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "release check passed (7 gates)" in result.stdout


def test_openapi_snapshot_exposes_the_evidence_loop() -> None:
    schema = json.loads(
        (PROJECT_ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")
    )

    assert schema["info"]["title"] == "PersonaOS"
    assert schema["info"]["version"] == "0.11.0"
    paths = schema["paths"]
    assert "post" in paths["/api/v1/personas"]
    assert "post" in paths["/api/v1/personas/{persona_id}/documents"]
    assert "post" in paths["/api/v1/memory-candidates/{memory_id}/review"]
    assert "post" in paths["/api/v1/conversations/{conversation_id}/messages"]
    assert "get" in paths["/api/v1/messages/{message_id}/citations"]
    assert "get" in paths["/api/v1/personas/{persona_id}/audit-events"]
