"""Run the free PersonaOS release smoke test through the Web origin.

The test intentionally creates one fictional persona and keeps it available so
the result can be inspected in the UI. It never calls a paid model.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SOURCE = PROJECT_ROOT / "examples" / "data" / "demo-journal.md"


class SmokeError(RuntimeError):
    """Raised when the deployed vertical flow violates its contract."""


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

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
                "X-Request-ID": f"compose-smoke-{uuid.uuid4()}",
            },
            timeout=20,
        )

    def text(self, path: str, *, timeout: float = 5) -> str:
        request = Request(
            f"{self.base_url}{path}",
            headers={"Accept": "text/html"},
        )
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

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
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
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


def run(base_url: str, timeout: float) -> dict[str, Any]:
    client = Client(base_url)
    health = wait_until_ready(client, timeout)
    suffix = uuid.uuid4().hex[:8]
    persona = client.json(
        "POST",
        "/api/v1/personas",
        {
            "display_name": f"Compose Smoke {suffix}",
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
        "web_origin": base_url.rstrip("/"),
        "runtime": health["runtime"],
        "persona_id": persona["id"],
        "document_id": document["document"]["id"],
        "confirmed_memory_id": confirmed["memory"]["id"],
        "answer_status": answer["assistant_message"]["answer_status"],
        "citation_id": citation["citation"]["citation_id"],
        "source_locator": citation["source"]["locator"],
        "audit_actions": sorted(actions),
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
    args = parser.parse_args()
    try:
        result = run(args.base_url, args.timeout)
    except (OSError, SmokeError, URLError) as exc:
        print(f"PersonaOS smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
