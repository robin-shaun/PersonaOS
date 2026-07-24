from __future__ import annotations

from typing import Protocol

from core.identity.models import PersonalContext


class PersonalContextProvider(Protocol):
    """Stable boundary used by business agents to obtain personal context."""

    def for_task(
        self,
        *,
        user_id: str,
        context: str,
    ) -> PersonalContext:
        ...
