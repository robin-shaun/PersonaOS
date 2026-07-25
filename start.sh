#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
SYSTEM_PYTHON="${PYTHON_BIN:-python3}"
RUNTIME_LOCK="${PROJECT_DIR}/requirements.lock"
LOCK_STAMP="${VENV_DIR}/.personaos-requirements.sha256"
FORCE_INSTALL=0

declare -a OWNED_PIDS=()

log() {
    printf '[PersonaOS] %s\n' "$*" >&2
}

die() {
    log "错误：$*"
    exit 1
}

show_help() {
    cat <<'EOF'
用法：./start.sh [--install]

一键启动 PersonaOS API、Worker，以及 Hermes 模式所需的本地 gateway。

选项：
  --install  强制重新安装项目运行依赖
  -h, --help 显示帮助

环境变量：
  PYTHON_BIN     创建虚拟环境时使用的 Python，默认 python3
  HERMES_PROFILE Hermes 本地 profile，默认 ai-colleague

启动后保持此前台脚本运行；按 Ctrl+C 会停止本脚本启动的全部服务。
EOF
}

while (($# > 0)); do
    case "$1" in
        --install)
            FORCE_INSTALL=1
            ;;
        -h | --help)
            show_help
            exit 0
            ;;
        *)
            show_help >&2
            die "未知参数：$1"
            ;;
    esac
    shift
done

cd -- "${PROJECT_DIR}"

[[ -f "${RUNTIME_LOCK}" ]] || die "缺少依赖锁文件：${RUNTIME_LOCK}"

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cp -- "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    log "已从 .env.example 创建 .env"
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
    command -v "${SYSTEM_PYTHON}" >/dev/null 2>&1 || {
        die "找不到 ${SYSTEM_PYTHON}，请先安装 Python 3.11 或更高版本"
    }
    "${SYSTEM_PYTHON}" -c '
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
' || die "需要 Python 3.11 或更高版本"
    log "正在创建虚拟环境 .venv"
    "${SYSTEM_PYTHON}" -m venv "${VENV_DIR}"
    FORCE_INSTALL=1
fi

"${VENV_PYTHON}" -c '
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
' || die ".venv 中的 Python 版本低于 3.11，请删除 .venv 后重试"

if ! "${VENV_PYTHON}" -c '
import alembic
import argon2
import cryptography
import fastapi
import httpx
import jwt
import multipart
import pgvector
import psycopg
import pydantic
import sqlalchemy
import uvicorn
import yaml
from dotenv import load_dotenv
' >/dev/null 2>&1; then
    FORCE_INSTALL=1
fi

LOCK_DIGEST="$("${VENV_PYTHON}" - "${RUNTIME_LOCK}" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
if [[ ! -f "${LOCK_STAMP}" ]] || [[ "$(<"${LOCK_STAMP}")" != "${LOCK_DIGEST}" ]]; then
    FORCE_INSTALL=1
fi

if ((FORCE_INSTALL)); then
    log "正在按 requirements.lock 安装项目运行依赖"
    "${VENV_PYTHON}" -m pip install \
        --index-url https://pypi.org/simple \
        --require-hashes \
        -r "${RUNTIME_LOCK}"
    "${VENV_PYTHON}" -m pip install \
        --index-url https://pypi.org/simple \
        --no-deps \
        --no-build-isolation \
        -e "${PROJECT_DIR}"
    printf '%s\n' "${LOCK_DIGEST}" >"${LOCK_STAMP}"
fi

CONFIG_OUTPUT="$({
    "${VENV_PYTHON}" - "${PROJECT_DIR}" <<'PY'
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from core.config import Settings


root = Path(sys.argv[1]).resolve()
load_dotenv(root / ".env", override=False)
settings = Settings.from_env(root)
if settings.runtime_name not in {"rules", "hermes"}:
    raise ValueError("DIGITAL_EMPLOYEE_RUNTIME must be rules or hermes")

api_host = settings.api_host.strip()
if not api_host or any(character in api_host for character in "\r\n\t"):
    raise ValueError("DIGITAL_EMPLOYEE_API_HOST is invalid")

probe_host = api_host
if api_host == "0.0.0.0":
    probe_host = "127.0.0.1"
elif api_host == "::":
    probe_host = "::1"
display_host = f"[{probe_host}]" if ":" in probe_host else probe_host
api_base_url = f"http://{display_host}:{settings.api_port}"

if settings.runtime_name == "hermes":
    if not settings.hermes_api_key:
        raise ValueError(
            "HERMES_API_KEY is required when DIGITAL_EMPLOYEE_RUNTIME=hermes"
        )
    if not settings.hermes_model:
        raise ValueError("HERMES_MODEL must not be empty")
    hermes_base_url = settings.hermes_api_url.rstrip("/")
    if hermes_base_url.endswith("/v1"):
        hermes_base_url = hermes_base_url[: -len("/v1")]
    parsed_hermes_url = urlsplit(hermes_base_url)
    if (
        parsed_hermes_url.scheme not in {"http", "https"}
        or not parsed_hermes_url.hostname
    ):
        raise ValueError("HERMES_API_URL must be an HTTP(S) URL")
    hermes_is_local = parsed_hermes_url.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    hermes_profile = (os.getenv("HERMES_PROFILE") or "ai-colleague").strip()
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
        hermes_profile,
    ):
        raise ValueError("HERMES_PROFILE is invalid")
else:
    hermes_base_url = "http://127.0.0.1:8642"
    hermes_is_local = False
    hermes_profile = "ai-colleague"

print(api_host)
print(settings.api_port)
print(settings.runtime_name)
print(api_base_url)
print(hermes_base_url)
print("1" if hermes_is_local else "0")
print(hermes_profile)
PY
} 2>&1)" || die "无法读取启动配置：${CONFIG_OUTPUT}"

mapfile -t CONFIG_LINES <<<"${CONFIG_OUTPUT}"
if ((${#CONFIG_LINES[@]} != 7)); then
    die "启动配置输出格式无效"
fi

API_HOST="${CONFIG_LINES[0]}"
API_PORT="${CONFIG_LINES[1]}"
RUNTIME_NAME="${CONFIG_LINES[2]}"
API_BASE_URL="${CONFIG_LINES[3]}"
HERMES_BASE_URL="${CONFIG_LINES[4]}"
HERMES_IS_LOCAL="${CONFIG_LINES[5]}"
HERMES_PROFILE_NAME="${CONFIG_LINES[6]}"

assert_api_port_available() {
    "${VENV_PYTHON}" - "${API_HOST}" "${API_PORT}" <<'PY'
from __future__ import annotations

import socket
import sys


host = sys.argv[1]
port = int(sys.argv[2])
family = socket.AF_INET6 if ":" in host else socket.AF_INET
with socket.socket(family, socket.SOCK_STREAM) as listener:
    try:
        listener.bind((host, port))
    except OSError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
PY
}

http_ready() {
    local url="$1"
    "${VENV_PYTHON}" - "${url}" <<'PY'
from __future__ import annotations

import sys

import httpx


try:
    response = httpx.get(sys.argv[1], timeout=1.0, trust_env=False)
except httpx.HTTPError:
    raise SystemExit(1)
raise SystemExit(0 if 200 <= response.status_code < 300 else 1)
PY
}

wait_for_http() {
    local url="$1"
    local timeout_seconds="$2"
    "${VENV_PYTHON}" - "${url}" "${timeout_seconds}" <<'PY'
from __future__ import annotations

import sys
import time

import httpx


url = sys.argv[1]
deadline = time.monotonic() + float(sys.argv[2])
last_error = "no response"
with httpx.Client(timeout=1.0, trust_env=False) as client:
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if 200 <= response.status_code < 300:
                print(response.text)
                raise SystemExit(0)
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.25)
print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

get_required() {
    local url="$1"
    "${VENV_PYTHON}" - "${url}" <<'PY'
from __future__ import annotations

import sys

import httpx


try:
    response = httpx.get(sys.argv[1], timeout=30.0, trust_env=False)
except httpx.HTTPError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
if not 200 <= response.status_code < 300:
    print(f"HTTP {response.status_code}: {response.text[:500]}", file=sys.stderr)
    raise SystemExit(1)
print(response.text)
PY
}

shutdown() {
    local exit_code=$?
    trap - EXIT INT TERM
    if ((${#OWNED_PIDS[@]} > 0)); then
        log "正在停止本次启动的服务"
        local index
        local pid
        for ((index = ${#OWNED_PIDS[@]} - 1; index >= 0; index--)); do
            pid="${OWNED_PIDS[index]}"
            if kill -0 "${pid}" 2>/dev/null; then
                kill -TERM "${pid}" 2>/dev/null || true
            fi
        done
        for pid in "${OWNED_PIDS[@]}"; do
            wait "${pid}" 2>/dev/null || true
        done
    fi
    exit "${exit_code}"
}

trap shutdown EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! assert_api_port_available; then
    die "${API_HOST}:${API_PORT} 已被占用，请修改 .env 中的 DIGITAL_EMPLOYEE_API_PORT"
fi

if [[ "${RUNTIME_NAME}" == "hermes" ]]; then
    if http_ready "${HERMES_BASE_URL}/health"; then
        log "复用已运行的 Hermes gateway：${HERMES_BASE_URL}"
    else
        if [[ "${HERMES_IS_LOCAL}" != "1" ]]; then
            die "远端 Hermes 不可用：${HERMES_BASE_URL}"
        fi
        command -v hermes >/dev/null 2>&1 || {
            die "Hermes 未运行且未找到 hermes 命令，请先按 docs/hermes.md 安装"
        }
        log "正在启动 Hermes profile：${HERMES_PROFILE_NAME}"
        hermes -p "${HERMES_PROFILE_NAME}" gateway &
        OWNED_PIDS+=("$!")
        if ! wait_for_http "${HERMES_BASE_URL}/health" 20 >/dev/null; then
            die "Hermes gateway 未能在 20 秒内就绪"
        fi
    fi
fi

log "正在检查并升级本地数据库 Schema"
if ! MIGRATION_MODE="$("${VENV_PYTHON}" -m core.storage.migration_bootstrap)"; then
    die "数据库 Schema 迁移失败；请先备份数据库并检查 Alembic 输出"
fi
if [[ "${MIGRATION_MODE}" == "migrated" ]]; then
    export DIGITAL_EMPLOYEE_AUTO_CREATE_SCHEMA=false
    log "数据库已通过 Alembic 升级并校验"
elif [[ "${MIGRATION_MODE}" == "legacy-create-all" ]]; then
    log "检测到人物功能前的兼容数据库；保留 create_all 启动模式"
else
    die "数据库迁移引导返回未知模式：${MIGRATION_MODE}"
fi

log "正在启动 API：${API_BASE_URL}"
"${VENV_PYTHON}" -m apps.api &
API_PID=$!
OWNED_PIDS+=("${API_PID}")

if ! wait_for_http "${API_BASE_URL}/health" 20 >/dev/null; then
    die "API 未能在 20 秒内就绪"
fi

if ! RUNTIME_STATUS="$(get_required "${API_BASE_URL}/health")"; then
    die "服务健康检查失败"
fi
log "Agent Runtime 已就绪：${RUNTIME_STATUS}"

if ! AUTH_STATUS="$(get_required "${API_BASE_URL}/api/v1/auth/status")"; then
    die "认证状态检查失败"
fi
if "${VENV_PYTHON}" -c '
import json
import sys
raise SystemExit(0 if json.load(sys.stdin)["setup_required"] else 1)
' <<<"${AUTH_STATUS}"; then
    log "尚未创建登录账户。请在另一个终端运行："
    log ".venv/bin/python -m apps.admin create-account --username admin --display-name Administrator --role admin"
fi

log "正在启动 Worker"
"${VENV_PYTHON}" -m apps.worker.run &
WORKER_PID=$!
OWNED_PIDS+=("${WORKER_PID}")

sleep 0.25
if ! kill -0 "${WORKER_PID}" 2>/dev/null; then
    wait "${WORKER_PID}" || true
    die "Worker 启动失败"
fi

log "启动完成"
log "API 文档：${API_BASE_URL}/docs"
log "按 Ctrl+C 停止所有服务"

set +e
wait -n "${OWNED_PIDS[@]}"
CHILD_EXIT_CODE=$?
set -e
if ((CHILD_EXIT_CODE == 0)); then
    CHILD_EXIT_CODE=1
fi
log "有服务意外退出，正在关闭其余服务"
exit "${CHILD_EXIT_CODE}"
