"""Restore a verified PersonaOS JSON export into a clean local deployment."""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
from typing import Any

import httpx


def restore(
    export_path: Path,
    *,
    base_url: str,
    username: str,
) -> dict[str, Any]:
    try:
        package = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read Persona export: {exc}") from exc
    if not isinstance(package, dict):
        raise SystemExit("Persona export must be a JSON object")
    password = getpass.getpass(f"Password for {username}: ")
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        if login.status_code != 200:
            raise SystemExit(f"Login failed ({login.status_code}): {login.text}")
        csrf_token = login.json()["csrf_token"]
        response = client.post(
            "/api/v1/personas/import",
            headers={"X-CSRF-Token": csrf_token},
            json=package,
        )
        if response.status_code != 201:
            raise SystemExit(
                f"Restore failed ({response.status_code}): {response.text}"
            )
        return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore a Persona export while preserving its stable identity. "
            "The target must not already contain that Persona UUID."
        )
    )
    parser.add_argument("export", type=Path, help="JSON file produced by PersonaOS")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--base-url", default="http://127.0.0.1:18110")
    args = parser.parse_args()
    result = restore(
        args.export.resolve(),
        base_url=args.base_url,
        username=args.username,
    )
    print(
        json.dumps(
            {
                "persona_id": result["persona"]["id"],
                "display_name": result["persona"]["display_name"],
                "identity_preserved": result["manifest"]["identity_preserved"],
                "restored": result["restored"],
                "indexing": result["indexing"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
