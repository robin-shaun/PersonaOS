from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
from uuid import uuid4

from dotenv import load_dotenv

from core.bootstrap import build_container
from core.config import Settings
from core.services.task_queue import TaskWorker


def parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the persistent digital employee task worker."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one queued task and exit.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.worker_poll_interval_seconds,
    )
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=settings.worker_lease_seconds,
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=settings.worker_retry_delay_seconds,
    )
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=settings.worker_task_timeout_seconds,
    )
    parser.add_argument(
        "--control-poll",
        type=float,
        default=settings.worker_control_poll_seconds,
    )
    parser.add_argument("--worker-id", default=_default_worker_id())
    return parser.parse_args()


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


async def run() -> None:
    load_dotenv()
    settings = Settings.from_env()
    args = parse_args(settings)
    container = build_container(settings=settings)
    worker = TaskWorker(
        store=container.store,
        project_maintenance=container.project_maintenance,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        retry_delay_seconds=args.retry_delay,
        task_timeout_seconds=args.task_timeout,
        control_poll_interval_seconds=args.control_poll,
    )
    if args.once:
        result = await worker.run_one()
        print(
            json.dumps(
                result or {"status": "idle", "worker_id": args.worker_id},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    await worker.run_forever(poll_interval_seconds=args.poll_interval)


if __name__ == "__main__":
    asyncio.run(run())
