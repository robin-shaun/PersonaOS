"""Run the free two-account PersonaOS smoke test through the Web origin.

The test creates fictional data in two independently authenticated workspaces,
proves the complete evidence loop in both, and probes cross-account resources.
It never calls a paid model.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SOURCE = PROJECT_ROOT / "examples" / "data" / "demo-journal.md"


class SmokeError(RuntimeError):
    """Raised when the deployed vertical flow violates its contract."""


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.csrf_token = ""
        self._opener = build_opener(
            HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def login(self, username: str, password: str) -> dict[str, Any]:
        result = self.json(
            "POST",
            "/api/v1/auth/login",
            {"username": username, "password": password},
        )
        self.csrf_token = str(result["csrf_token"])
        return result

    def json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 10,
    ) -> Any:
        body = None
        headers = {
            "Accept": "application/json",
            "X-Request-ID": f"compose-smoke-{uuid.uuid4()}",
        }
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["Origin"] = self.base_url
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self._send(method, path, body, headers, timeout=timeout)

    def upload(self, path: str, source: Path) -> Any:
        boundary = f"personaos-smoke-{uuid.uuid4().hex}"
        filename = source.name
        media_type = mimetypes.guess_type(filename)[0] or "text/plain"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode()
        body += source.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode()
        return self._send(
            "POST",
            path,
            body,
            {
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Origin": self.base_url,
                "X-CSRF-Token": self.csrf_token,
                "X-Request-ID": f"compose-smoke-{uuid.uuid4()}",
            },
            timeout=20,
        )

    def text(self, path: str, *, timeout: float = 5) -> str:
        request = Request(
            f"{self.base_url}{path}",
            headers={"Accept": "text/html"},
        )
        with self._opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    def require_http_error(
        self,
        method: str,
        path: str,
        expected_status: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.json(method, path, payload)
        except SmokeError as exc:
            require(
                f"HTTP {expected_status}:" in str(exc),
                f"{method} {path} did not return HTTP {expected_status}: {exc}",
            )
            return
        raise SmokeError(
            f"{method} {path} unexpectedly crossed the account boundary"
        )

    def _send(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        *,
        timeout: float,
    ) -> Any:
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                content = response.read().decode("utf-8")
                return json.loads(content) if content else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeError(
                f"{method} {path} returned HTTP {exc.code}: {detail}"
            ) from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def wait_until_ready(client: Client, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            index = client.text("/")
            health = client.json("GET", "/health", timeout=5)
            require('<div id="root"></div>' in index, "Web index is not PersonaOS")
            require(health.get("status") == "ok", "API health is not ok")
            return health
        except (OSError, URLError, SmokeError) as exc:
            last_error = str(exc)
            time.sleep(1)
    raise SmokeError(f"services did not become ready: {last_error}")


def wait_for_task(
    client: Client,
    task_id: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status = "unknown"
    while time.monotonic() < deadline:
        bundle = client.json("GET", f"/api/v1/tasks/{task_id}")
        last_status = bundle["task"]["status"]
        if last_status == "completed":
            return bundle
        if last_status in {"failed", "cancelled", "rejected"}:
            raise SmokeError(f"ingestion task ended as {last_status}")
        time.sleep(0.5)
    raise SmokeError(f"ingestion task remained {last_status}")


def exercise_evidence_loop(
    client: Client,
    *,
    label: str,
    timeout: float,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    persona = client.json(
        "POST",
        "/api/v1/personas",
        {
            "display_name": f"Compose Smoke {label} {suffix}",
            "description": "PersonaOS 空环境纵向验收使用的虚构人物。",
        },
    )
    require("不是现实中的本人" in persona["simulation_notice"], "notice missing")

    upload = client.upload(
        f"/api/v1/personas/{persona['id']}/documents",
        DEMO_SOURCE,
    )
    require(upload["document"]["status"] == "uploaded", "upload was not queued")
    wait_for_task(client, upload["queue_submission"]["task_id"], timeout=timeout)

    document = client.json(
        "GET",
        f"/api/v1/documents/{upload['document']['id']}",
    )
    require(document["document"]["status"] == "ready", "document is not ready")
    require(bool(document["chunks"]), "document has no stable chunks")

    candidates = client.json(
        "GET",
        f"/api/v1/personas/{persona['id']}/memory-candidates",
    )
    require(bool(candidates), "ingestion produced no review candidates")
    candidate = candidates[0]
    confirmed = client.json(
        "POST",
        f"/api/v1/memory-candidates/{candidate['memory']['id']}/review",
        {
            "action": "confirm",
            "reason": "Compose smoke 明确确认虚构演示资料。",
        },
    )
    require(confirmed["memory"]["status"] == "confirmed", "review did not confirm")
    require(bool(confirmed["evidence"]), "confirmed memory lost its evidence")

    conversation = client.json(
        "POST",
        f"/api/v1/personas/{persona['id']}/conversations",
        {"title": "PersonaOS Compose smoke"},
    )
    answer = client.json(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/messages",
        {"content": "我什么时候加入 PersonaOS 项目？", "top_k": 5},
        timeout=30,
    )
    require(
        answer["assistant_message"]["answer_status"] == "answered",
        "confirmed memory was not used for an answer",
    )
    require(bool(answer["citations"]), "answer has no resolvable citations")
    citation = answer["citations"][0]
    require(
        citation["source"]["filename"] == DEMO_SOURCE.name,
        "citation does not resolve to the uploaded source",
    )
    exported = client.json(
        "POST",
        f"/api/v1/personas/{persona['id']}/export",
        {"include_raw_sources": True},
        timeout=30,
    )
    require(
        exported["manifest"]["included_raw_sources"] is True,
        "recent login did not authorize raw-source export",
    )

    audits = client.json(
        "GET",
        f"/api/v1/personas/{persona['id']}/audit-events",
    )
    actions = {event["action"] for event in audits}
    require(
        {
            "persona.created",
            "document.uploaded",
            "document.processed",
            "memory.confirmed",
            "question.answered",
        }.issubset(actions),
        "audit trail is incomplete",
    )
    return {
        "persona_id": persona["id"],
        "document_id": document["document"]["id"],
        "task_id": upload["queue_submission"]["task_id"],
        "confirmed_memory_id": confirmed["memory"]["id"],
        "conversation_id": conversation["id"],
        "assistant_message_id": answer["assistant_message"]["id"],
        "answer_status": answer["assistant_message"]["answer_status"],
        "citation_id": citation["citation"]["citation_id"],
        "source_locator": citation["source"]["locator"],
        "audit_actions": sorted(actions),
    }


def assert_cross_account_denied(
    client: Client,
    other: dict[str, Any],
) -> None:
    probes: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
        ("GET", f"/api/v1/personas/{other['persona_id']}", None),
        ("GET", f"/api/v1/documents/{other['document_id']}", None),
        ("GET", f"/api/v1/memories/{other['confirmed_memory_id']}", None),
        ("GET", f"/api/v1/tasks/{other['task_id']}", None),
        (
            "GET",
            f"/api/v1/conversations/{other['conversation_id']}/messages",
            None,
        ),
        (
            "GET",
            f"/api/v1/messages/{other['assistant_message_id']}/citations",
            None,
        ),
        (
            "GET",
            f"/api/v1/personas/{other['persona_id']}/audit-events",
            None,
        ),
        (
            "POST",
            f"/api/v1/personas/{other['persona_id']}/export",
            {"include_raw_sources": True},
        ),
        (
            "DELETE",
            f"/api/v1/documents/{other['document_id']}?confirm=true",
            None,
        ),
        (
            "DELETE",
            (
                f"/api/v1/memories/"
                f"{other['confirmed_memory_id']}?confirm=true"
            ),
            None,
        ),
        (
            "POST",
            f"/api/v1/tasks/{other['task_id']}/cancel",
            {"reason": "cross-account smoke probe"},
        ),
    )
    for method, path, payload in probes:
        client.require_http_error(method, path, 404, payload)


def run(
    base_url: str,
    timeout: float,
    *,
    admin_username: str,
    admin_password: str,
    member_username: str,
    member_password: str,
) -> dict[str, Any]:
    readiness_client = Client(base_url)
    health = wait_until_ready(readiness_client, timeout)
    admin_client = Client(base_url)
    member_client = Client(base_url)
    admin_session = admin_client.login(admin_username, admin_password)
    member_session = member_client.login(member_username, member_password)
    require(
        admin_session["account"]["username"] == admin_username,
        "admin login resolved the wrong account",
    )
    require(
        member_session["account"]["username"] == member_username,
        "member login resolved the wrong account",
    )

    admin_loop = exercise_evidence_loop(
        admin_client,
        label="admin",
        timeout=timeout,
    )
    member_loop = exercise_evidence_loop(
        member_client,
        label="member",
        timeout=timeout,
    )
    require(
        admin_loop["task_id"] != member_loop["task_id"],
        "content-identical ingestion replayed across accounts",
    )
    assert_cross_account_denied(admin_client, member_loop)
    assert_cross_account_denied(member_client, admin_loop)
    require(
        {item["id"] for item in admin_client.json("GET", "/api/v1/personas")}
        == {admin_loop["persona_id"]},
        "admin persona listing crossed account boundaries",
    )
    require(
        {item["id"] for item in member_client.json("GET", "/api/v1/personas")}
        == {member_loop["persona_id"]},
        "member persona listing crossed account boundaries",
    )
    return {
        "web_origin": base_url.rstrip("/"),
        "runtime": health["runtime"],
        "accounts": {
            admin_username: admin_loop,
            member_username: member_loop,
        },
        "cross_account_probes": 22,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the PersonaOS release flow through the Web origin."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:18111",
        help="Web origin exposed by Compose.",
    )
    parser.add_argument(
        "--timeout",
        default=120.0,
        type=float,
        help="Maximum seconds to wait for services and ingestion.",
    )
    parser.add_argument(
        "--admin-username",
        default=os.getenv("PERSONAOS_SMOKE_ADMIN_USERNAME", "smoke-admin"),
    )
    parser.add_argument(
        "--member-username",
        default=os.getenv("PERSONAOS_SMOKE_MEMBER_USERNAME", "smoke-member"),
    )
    args = parser.parse_args()
    try:
        admin_password = os.getenv("PERSONAOS_SMOKE_ADMIN_PASSWORD", "")
        member_password = os.getenv("PERSONAOS_SMOKE_MEMBER_PASSWORD", "")
        require(
            bool(admin_password and member_password),
            "smoke passwords must be supplied through environment variables",
        )
        result = run(
            args.base_url,
            args.timeout,
            admin_username=args.admin_username,
            admin_password=admin_password,
            member_username=args.member_username,
            member_password=member_password,
        )
    except (OSError, SmokeError, URLError) as exc:
        print(f"PersonaOS smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
