from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "examples" / "data" / "demo-journal.md"


def _required(response: httpx.Response) -> Any:
    response.raise_for_status()
    return response.json()


def run_demo(base_url: str, source_path: Path) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
        persona = _required(
            client.post(
                "/api/v1/personas",
                json={
                    "display_name": "PersonaOS 演示人物",
                    "description": "只依据用户确认过的演示资料。",
                },
            )
        )
        source = source_path.read_bytes()
        upload = _required(
            client.post(
                f"/api/v1/personas/{persona['id']}/documents",
                files={
                    "file": (
                        source_path.name,
                        source,
                        "text/markdown",
                    )
                },
            )
        )
        task_id = upload["queue_submission"]["task_id"]
        deadline = time.monotonic() + 30
        while True:
            task = _required(client.get(f"/api/v1/tasks/{task_id}"))
            task_status = task["task"]["status"]
            if task_status == "completed":
                break
            if task_status in {"failed", "cancelled", "rejected"}:
                raise RuntimeError(f"ingestion task ended with status {task_status}")
            if time.monotonic() >= deadline:
                raise TimeoutError("ingestion did not complete within 30 seconds")
            time.sleep(0.25)

        candidates = _required(
            client.get(f"/api/v1/personas/{persona['id']}/memory-candidates")
        )
        if not candidates:
            raise RuntimeError("ingestion completed without memory candidates")
        confirmed = _required(
            client.post(
                (f"/api/v1/memory-candidates/{candidates[0]['memory']['id']}/review"),
                json={
                    "action": "confirm",
                    "reason": "PersonaOS 最小演示确认。",
                },
            )
        )
        conversation = _required(
            client.post(
                f"/api/v1/personas/{persona['id']}/conversations",
                json={"title": "PersonaOS 最小问答演示"},
            )
        )
        answer = _required(
            client.post(
                f"/api/v1/conversations/{conversation['id']}/messages",
                json={
                    "content": "我什么时候加入 PersonaOS，负责什么？",
                    "top_k": 3,
                },
            )
        )
        audits = _required(client.get(f"/api/v1/personas/{persona['id']}/audit-events"))
        first_evidence = confirmed["evidence"][0]
        return {
            "persona": {
                "id": persona["id"],
                "display_name": persona["display_name"],
                "simulation_notice": persona["simulation_notice"],
            },
            "document": upload["document"],
            "candidate_count": len(candidates),
            "confirmed_memory": {
                "id": confirmed["memory"]["id"],
                "memory_type": confirmed["memory"]["memory_type"],
                "version": confirmed["current_version"]["version"],
                "summary": confirmed["current_version"]["structured_summary"],
                "epistemic_status": confirmed["memory"]["epistemic_status"],
            },
            "citation": {
                "source_filename": first_evidence["source_document"][
                    "original_filename"
                ],
                "locator": first_evidence["evidence"]["locator_snapshot"],
                "excerpt": first_evidence["evidence"]["excerpt"],
            },
            "answer": {
                "content": answer["assistant_message"]["content"],
                "answer_status": answer["assistant_message"]["answer_status"],
                "uncertainty": answer["assistant_message"]["uncertainty"],
                "citations": answer["citations"],
                "embedding_space_id": answer["retrieval_run"]["embedding_space_id"],
            },
            "audit_actions": [event["action"] for event in audits],
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the evidence-bound PersonaOS M2 question-answer demo."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:18110",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
    )
    args = parser.parse_args()
    result = run_demo(args.base_url.rstrip("/"), args.source.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
