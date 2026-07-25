from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Authenticated actor boundary consumed by persona domain services.

    The local MVP uses one server-configured owner. Keeping this object independent
    from FastAPI makes a future login/session adapter replaceable without
    changing memory and ingestion services.
    """

    owner_id: str
    actor_id: str
    actor_type: str = "local_user"
    request_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not self.actor_id.strip():
            raise ValueError("actor_id must not be empty")
