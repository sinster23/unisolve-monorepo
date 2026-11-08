"""HTTP transport for the Codeforces API. Contains no business logic.

Codeforces allows roughly one request per two seconds, so every request goes
through a throttle, and rate-limited replies are retried a bounded number of
times. Requests are therefore sequential: do not gather() calls on one client.
"""

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.scrapers.codeforces.exceptions import (
    APIError,
    RateLimitedError,
    RequestError,
    ResponseParsingError,
    UserNotFoundError,
)
from app.scrapers.codeforces.queries import BASE_URL

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
    ),
}

# HTTP statuses that mean "slow down" (Codeforces answers 503 when the call limit is hit).
_RATE_LIMIT_STATUSES = (429, 503)


class CodeforcesClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float = 2.0,
        max_retries: int = 2,
        retry_delay: float = 2.0,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.AsyncClient(
            base_url=BASE_URL + "/",
            timeout=httpx.Timeout(self.settings.request_timeout),
            headers=_HEADERS,
            transport=transport,
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def __aenter__(self) -> "CodeforcesClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _throttle(self) -> None:
        """Space request starts at least `min_interval` seconds apart."""
        async with self._lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = time.monotonic() + self._min_interval

    async def execute(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call an API method and return its `result`.

        Retries only rate-limited replies; every other error is raised at once.
        """
        for attempt in range(self._max_retries + 1):
            try:
                return await self._request(method, params)
            except RateLimitedError:
                if attempt == self._max_retries:
                    raise
                await asyncio.sleep(self._retry_delay * (2**attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _request(self, method: str, params: dict[str, Any] | None) -> Any:
        await self._throttle()
        try:
            response = await self._http.get(method, params=params)
        except httpx.TimeoutException as exc:
            raise RequestError("Codeforces request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"Codeforces request failed ({type(exc).__name__}).") from exc

        status = response.status_code

        # Codeforces reports API errors as JSON ({"status": "FAILED", "comment": ...}),
        # often with HTTP 400, so read the body before judging the HTTP status.
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if not isinstance(payload, dict):
            if status in _RATE_LIMIT_STATUSES:
                raise RateLimitedError(
                    f"Codeforces rate limit or outage (HTTP {status}).", status_code=status
                )
            if status >= 400:
                raise RequestError(f"Codeforces returned HTTP {status}.", status_code=status)
            raise ResponseParsingError("Codeforces response was not a JSON object.")

        if payload.get("status") == "FAILED":
            comment = str(payload.get("comment") or "")
            lowered = comment.lower()
            if "limit exceeded" in lowered:
                raise RateLimitedError(
                    "Codeforces rate limit reached (call limit exceeded).", status_code=status
                )
            if "not found" in lowered:
                raise UserNotFoundError("Codeforces user was not found.")
            raise APIError(f"Codeforces API error: {comment[:300]}", comment)

        if status in _RATE_LIMIT_STATUSES:
            raise RateLimitedError(f"Codeforces rate limit or outage (HTTP {status}).", status)
        if status >= 400:
            raise RequestError(f"Codeforces returned HTTP {status}.", status_code=status)

        if payload.get("status") != "OK" or "result" not in payload:
            raise ResponseParsingError("Codeforces response had no OK status and result.")
        return payload["result"]