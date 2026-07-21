from __future__ import annotations

import uvicorn
from dotenv import load_dotenv

from core.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    uvicorn.run(
        "apps.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
