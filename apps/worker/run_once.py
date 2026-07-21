from __future__ import annotations

import argparse
import asyncio
import json

from dotenv import load_dotenv

from core.bootstrap import build_container
from core.services.project_maintenance import ProjectMaintenanceCommand


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one read-only GitHub project maintenance task."
    )
    parser.add_argument("repository", help="GitHub repository in owner/name format")
    parser.add_argument("--user-id", default="local-user")
    parser.add_argument("--max-items", type=int, default=50)
    return parser.parse_args()


async def run() -> None:
    load_dotenv()
    args = parse_args()
    container = build_container()
    bundle = await container.project_maintenance.create_and_run(
        ProjectMaintenanceCommand(
            repository=args.repository,
            user_id=args.user_id,
            max_items=args.max_items,
        )
    )
    print(json.dumps(bundle, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
