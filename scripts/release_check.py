#!/usr/bin/env python3
"""Fail fast when the repository is not a coherent PersonaOS release."""

from __future__ import annotations

import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from export_openapi import canonical_schema_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.11.0"
EXPECTED_PYTHON_IMAGE = (
    "python:3.11.15-slim-bookworm@"
    "sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"
)
EXPECTED_NODE_IMAGE = (
    "node:22.23.1-alpine3.24@"
    "sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2"
)
EXPECTED_NGINX_IMAGE = (
    "nginxinc/nginx-unprivileged:1.31.3-alpine3.24@"
    "sha256:18d67281256ded39ff65e010ae4f831be18f19356f83c60bc546492c7eb6dd23"
)
EXPECTED_DATABASE_IMAGE = (
    "pgvector/pgvector:0.8.5-pg17-bookworm@"
    "sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0"
)
REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "ROADMAP.md",
    "SECURITY.md",
    "docs/api.md",
    "docs/architecture.svg",
    "docs/openapi.json",
    "docs/releases/v0.11.0.md",
    "docs/skill-development.md",
    "requirements.lock",
    "requirements-dev.lock",
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
)


class ReleaseCheckError(RuntimeError):
    """A release invariant was not met."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseCheckError(message)


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def check_required_files() -> None:
    for relative_path in REQUIRED_FILES:
        path = PROJECT_ROOT / relative_path
        require(path.is_file(), f"missing release file: {relative_path}")
        require(path.stat().st_size > 32, f"release file is empty: {relative_path}")


def check_versions() -> None:
    pyproject = tomllib.loads(read("pyproject.toml"))
    package = json.loads(read("apps/web/package.json"))
    package_lock = json.loads(read("apps/web/package-lock.json"))
    compose = yaml.safe_load(read("compose.yaml"))
    openapi = json.loads(read("docs/openapi.json"))

    versions: dict[str, Any] = {
        "pyproject": pyproject["project"]["version"],
        "python runtime": re.search(
            r'^VERSION = "([^"]+)"$',
            read("core/version.py"),
            re.MULTILINE,
        ).group(1),
        "web package": package["version"],
        "web lock": package_lock["version"],
        "web lock root": package_lock["packages"][""]["version"],
        "web runtime": re.search(
            r'^export const VERSION = "([^"]+)";$',
            read("apps/web/src/version.ts"),
            re.MULTILINE,
        ).group(1),
        "OpenAPI": openapi["info"]["version"],
    }
    for surface, version in versions.items():
        require(
            version == EXPECTED_VERSION,
            f"{surface} version is {version!r}, expected {EXPECTED_VERSION!r}",
        )
    require(read(".python-version").strip() == "3.11.15", "Python pin is stale")
    require(read(".node-version").strip() == "22.23.1", "Node.js pin is stale")

    expected_app_image = f"personaos:{EXPECTED_VERSION}"
    expected_web_image = f"personaos-web:{EXPECTED_VERSION}"
    services = compose["services"]
    require(services["api"]["image"] == expected_app_image, "API image tag is stale")
    require(
        services["worker"]["image"] == expected_app_image,
        "worker image tag is stale",
    )
    require(services["web"]["image"] == expected_web_image, "web image tag is stale")


def check_license_and_community_files() -> None:
    license_text = read("LICENSE")
    notice = read("NOTICE")
    pyproject = tomllib.loads(read("pyproject.toml"))

    require(
        license_text.startswith("Apache License\nVersion 2.0, January 2004"),
        "LICENSE is not the canonical Apache-2.0 text",
    )
    require(
        "http://www.apache.org/licenses/" in license_text,
        "LICENSE is missing the Apache-2.0 canonical URL",
    )
    require("PersonaOS" in notice, "NOTICE does not identify PersonaOS")
    require(
        pyproject["project"]["license"] == "Apache-2.0",
        "pyproject license does not match LICENSE",
    )
    require(
        pyproject["project"]["license-files"] == ["LICENSE", "NOTICE"],
        "distribution metadata does not include LICENSE and NOTICE",
    )
    security = read("SECURITY.md")
    require(
        "/security/advisories/new" in security,
        "SECURITY.md has no private reporting channel",
    )


def requirement_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith(("#", "--")):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def check_python_locks() -> None:
    for relative_path in ("requirements.lock", "requirements-dev.lock"):
        lock = read(relative_path)
        require(
            "--trusted-host" not in lock,
            f"{relative_path} contains a trusted-host override",
        )
        require(
            "--extra-index-url" not in lock,
            f"{relative_path} contains an extra package index",
        )
        indexes = re.findall(r"^--index-url[= ]+(\S+)", lock, re.MULTILINE)
        require(
            all(index == "https://pypi.org/simple" for index in indexes),
            f"{relative_path} contains an unapproved package index",
        )
        require("http://" not in lock, f"{relative_path} contains an insecure URL")
        blocks = requirement_blocks(lock)
        require(blocks, f"{relative_path} contains no requirements")
        for block in blocks:
            first_line = block.splitlines()[0]
            require("==" in first_line, f"unlocked requirement in {relative_path}: {first_line}")
            require(
                "--hash=sha256:" in block,
                f"unhashed requirement in {relative_path}: {first_line}",
            )
        for package in ("setuptools==", "wheel=="):
            require(package in lock, f"{relative_path} does not lock {package[:-2]}")

    dev_lock = read("requirements-dev.lock")
    for package in ("pip-audit==", "pytest==", "ruff=="):
        require(package in dev_lock, f"requirements-dev.lock does not lock {package[:-2]}")


def check_container_pins() -> None:
    backend = read("Dockerfile")
    frontend = read("apps/web/Dockerfile")
    compose = yaml.safe_load(read("compose.yaml"))

    require(
        f"FROM {EXPECTED_PYTHON_IMAGE}" in backend,
        "backend base image is not pinned to the approved digest",
    )
    require(
        "--require-hashes -r requirements.lock" in backend,
        "backend image does not consume the hashed lock",
    )
    require(
        f"FROM {EXPECTED_NODE_IMAGE}" in frontend,
        "Node base image is not pinned to the approved digest",
    )
    require(
        f"FROM {EXPECTED_NGINX_IMAGE}" in frontend,
        "Nginx base image is not pinned to the approved digest",
    )
    require(
        compose["services"]["db"]["image"] == EXPECTED_DATABASE_IMAGE,
        "PostgreSQL/pgvector image is not pinned to the approved digest",
    )


def check_ci_supply_chain() -> None:
    workflow = read(".github/workflows/ci.yml")
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
    require(uses, "CI workflow has no actions")
    for action in uses:
        if action.startswith("./"):
            continue
        require(
            re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) is not None,
            f"CI action is not pinned to a full commit SHA: {action}",
        )
    require(
        re.search(r"^permissions:\s*\n\s+contents:\s+read\s*$", workflow, re.MULTILINE)
        is not None,
        "CI does not set read-only default contents permission",
    )
    require("pull-requests: write" not in workflow, "CI grants pull request write")
    require("contents: write" not in workflow, "CI grants contents write")
    for gate in (
        "python scripts/release_check.py",
        "python scripts/export_openapi.py --check",
        "pip-audit --require-hashes -r requirements.lock",
        "npm audit --audit-level=high",
        "python3 examples/compose_smoke.py",
    ):
        require(gate in workflow, f"CI is missing gate: {gate}")


def check_docs_and_openapi() -> None:
    readme = read("README.md")
    for required_text in (
        "五分钟",
        "docs/architecture.svg",
        "不声称",
        "运行时仍可能",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ):
        require(required_text in readme, f"README is missing: {required_text}")

    architecture_root = ET.fromstring(read("docs/architecture.svg"))
    namespace = "{http://www.w3.org/2000/svg}"
    require(
        architecture_root.find(f"{namespace}title") is not None,
        "architecture.svg has no accessible title",
    )
    require(
        architecture_root.find(f"{namespace}desc") is not None,
        "architecture.svg has no accessible description",
    )
    require(
        read("docs/openapi.json") == canonical_schema_text(),
        "docs/openapi.json is stale; run python scripts/export_openapi.py",
    )
    check_internal_markdown_links()


def check_internal_markdown_links() -> None:
    excluded_parts = {".git", ".venv", "build", "node_modules"}
    link_pattern = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
    for document in PROJECT_ROOT.rglob("*.md"):
        if excluded_parts.intersection(document.relative_to(PROJECT_ROOT).parts):
            continue
        for raw_target in link_pattern.findall(
            document.read_text(encoding="utf-8")
        ):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0])
            resolved = (document.parent / target).resolve()
            require(
                resolved.exists(),
                (
                    f"broken relative link in "
                    f"{document.relative_to(PROJECT_ROOT)}: {raw_target}"
                ),
            )


def run() -> list[str]:
    checks = (
        ("release files", check_required_files),
        ("versions", check_versions),
        ("license and community policy", check_license_and_community_files),
        ("hashed Python locks", check_python_locks),
        ("container digests", check_container_pins),
        ("CI supply chain", check_ci_supply_chain),
        ("documentation and OpenAPI", check_docs_and_openapi),
    )
    completed: list[str] = []
    for name, check in checks:
        check()
        completed.append(name)
    return completed


def main() -> int:
    try:
        completed = run()
    except (OSError, KeyError, AttributeError, ValueError, ReleaseCheckError) as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    print(f"release check passed ({len(completed)} gates): {', '.join(completed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
