#!/usr/bin/env python3
"""Export PersonaOS OpenAPI with deterministic formatting."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "openapi.json"


def canonical_schema_text() -> str:
    """Build the application schema without touching the developer database."""

    with tempfile.TemporaryDirectory(prefix="personaos-openapi-") as directory:
        temporary_root = Path(directory)
        environment = {
            "DIGITAL_EMPLOYEE_DATABASE_URL": (
                f"sqlite:///{temporary_root / 'openapi.db'}"
            ),
            "DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA": "true",
            "DIGITAL_EMPLOYEE_RUNTIME": "rules",
            "PERSONA_BLOB_DIR": str(temporary_root / "blobs"),
            "PERSONA_BLOB_KEY_PATH": str(temporary_root / "blob.key"),
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from apps.api.main import app

            schema = app.openapi()
        finally:
            sys.path.remove(str(PROJECT_ROOT))
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    return json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export or verify the committed PersonaOS OpenAPI snapshot."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when docs/openapi.json differs from the runtime schema",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="schema output path (default: docs/openapi.json)",
    )
    arguments = parser.parse_args()
    expected = canonical_schema_text()
    output = arguments.output.resolve()

    if arguments.check:
        if not output.exists():
            print(f"OpenAPI snapshot is missing: {output}", file=sys.stderr)
            return 1
        if output.read_text(encoding="utf-8") != expected:
            print(
                "OpenAPI snapshot is stale; run "
                "python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI snapshot is current: {output.relative_to(PROJECT_ROOT)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    print(f"Wrote {output.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
