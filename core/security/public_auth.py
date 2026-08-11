from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TURNSTILE_SITEVERIFY_URL = (
    "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)


@dataclass(frozen=True, slots=True)
class TurnstileVerifier:
    """Validate single-use Cloudflare Turnstile tokens at the server boundary."""

    secret_key: str | None
    timeout_seconds: float = 5.0
    endpoint: str = TURNSTILE_SITEVERIFY_URL

    async def verify(self, token: str, *, remote_ip: str | None = None) -> bool:
        normalized = token.strip()
        if self.secret_key is None or not normalized or len(normalized) > 2048:
            return False
        payload = {
            "secret": self.secret_key,
            "response": normalized,
        }
        if remote_ip:
            payload["remoteip"] = remote_ip

        def submit() -> bool:
            request = Request(
                self.endpoint,
                data=urlencode(payload).encode("ascii"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (
                HTTPError,
                URLError,
                OSError,
                TimeoutError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                return False
            return isinstance(result, dict) and result.get("success") is True

        return await asyncio.to_thread(submit)


class SlidingWindowRateLimiter:
    """Small single-process guard; Cloudflare remains the distributed edge limit."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        max_keys: int = 10_000,
    ) -> None:
        if limit < 1 or window_seconds < 1 or max_keys < 1:
            raise ValueError("rate limiter values must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @property
    def window_seconds(self) -> int:
        return self._window_seconds

    def allow(self, key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self._window_seconds
        with self._lock:
            if key not in self._attempts and len(self._attempts) >= self._max_keys:
                for candidate, candidate_attempts in list(self._attempts.items()):
                    while candidate_attempts and candidate_attempts[0] <= cutoff:
                        candidate_attempts.popleft()
                    if not candidate_attempts:
                        del self._attempts[candidate]
                if len(self._attempts) >= self._max_keys:
                    return False
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._limit:
                return False
            attempts.append(timestamp)
            return True
