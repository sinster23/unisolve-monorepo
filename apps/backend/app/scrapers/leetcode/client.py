"""HTTP/GraphQL transport for LeetCode. Contains no business logic."""

from typing import Any

import httpx

from app.config import Settings, get_settings
from app.scrapers.leetcode.exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    RateLimitedError,
    RequestError,
    ResponseParsingError,
)

GRAPHQL_URL = "https://leetcode.com/graphql"

_BASE_HEADERS = {
    "Referer": "https://leetcode.com/",
    "Origin": "https://leetcode.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
    ),
}

# Substrings of GraphQL error messages that indicate a rejected session.
_AUTH_HINTS = ("sign in", "log in", "login", "not signed", "unauthor", "authenticat", "permission")


class LeetCodeClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.request_timeout),
            headers=_BASE_HEADERS,
            transport=transport,
        )

    async def __aenter__(self) -> "LeetCodeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    def _auth_headers(self) -> dict[str, str]:
        if not self.settings.has_leetcode_credentials:
            raise ConfigurationError(
                "This request needs LeetCode credentials. Set LEETCODE_SESSION and "
                "LEETCODE_CSRF_TOKEN in the root .env file."
            )
        session = self.settings.leetcode_session.get_secret_value().strip()  # type: ignore[union-attr]
        csrf = self.settings.leetcode_csrf_token.get_secret_value().strip()  # type: ignore[union-attr]
        # Sent per request, only when asked for, so public calls never carry cookies.
        return {"Cookie": f"LEETCODE_SESSION={session}; csrftoken={csrf}", "x-csrftoken": csrf}

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        authenticated: bool = False,
        referer: str | None = None,
    ) -> dict[str, Any]:
        """Run a GraphQL query and return its `data` object."""
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        if authenticated:
            headers.update(self._auth_headers())

        try:
            response = await self._http.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise RequestError("LeetCode request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"LeetCode request failed ({type(exc).__name__}).") from exc

        status = response.status_code
        if status in (401, 403) and authenticated:
            raise AuthenticationError(
                f"LeetCode rejected the session (HTTP {status}); it may be invalid, "
                "expired or blocked. Refresh LEETCODE_SESSION and LEETCODE_CSRF_TOKEN."
            )
        if status == 429:
            raise RateLimitedError("LeetCode rate limit reached (HTTP 429).", status_code=status)
        if status >= 400:
            raise RequestError(f"LeetCode returned HTTP {status}.", status_code=status)

        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponseParsingError("LeetCode response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ResponseParsingError("LeetCode response was not a JSON object.")

        errors = payload.get("errors")
        if errors:
            messages = [
                str(err.get("message", "")) if isinstance(err, dict) else str(err)
                for err in (errors if isinstance(errors, list) else [errors])
            ]
            if authenticated and any(h in m.lower() for m in messages for h in _AUTH_HINTS):
                raise AuthenticationError(
                    "LeetCode reported an authentication problem: " + "; ".join(messages)[:200]
                )
            raise APIError("LeetCode GraphQL error: " + "; ".join(messages)[:300], messages)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise APIError("LeetCode returned no data.")
        return data