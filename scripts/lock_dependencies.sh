#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_TOOL_DIR="$(mktemp -d -t personaos-lock-XXXXXX)"
LOCK_PYTHON="${LOCK_TOOL_DIR}/bin/python"

cleanup() {
    rm -rf -- "${LOCK_TOOL_DIR}"
}
trap cleanup EXIT

cd -- "${PROJECT_DIR}"
export PIP_CONFIG_FILE=/dev/null
export PIP_INDEX_URL=https://pypi.org/simple
unset PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST

python3 -m venv "${LOCK_TOOL_DIR}"
"${LOCK_PYTHON}" -m pip install --disable-pip-version-check \
    --quiet \
    --index-url https://pypi.org/simple \
    "pip-tools==7.6.0"

export CUSTOM_COMPILE_COMMAND="./scripts/lock_dependencies.sh"

COMMON_ARGUMENTS=(
    --all-build-deps
    --allow-unsafe
    --generate-hashes
    --index-url=https://pypi.org/simple
    --quiet
    --resolver=backtracking
    --strip-extras
)

"${LOCK_TOOL_DIR}/bin/pip-compile" \
    "${COMMON_ARGUMENTS[@]}" \
    --output-file=requirements.lock \
    pyproject.toml

"${LOCK_TOOL_DIR}/bin/pip-compile" \
    "${COMMON_ARGUMENTS[@]}" \
    --all-extras \
    --output-file=requirements-dev.lock \
    pyproject.toml
