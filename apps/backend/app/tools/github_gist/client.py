"""HTTP client for reading and managing GitHub Gists."""

from collections.abc import Mapping
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.tools.github_gist.exceptions import (
    AuthenticationError,
    ConfigurationError,
    RequestError,
    ResponseParsingError,
)
from app.tools.github_gist.models import Gist

BASE_URL = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "unisolve-backend",
}


class GitHubGistClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(self.settings.request_timeout),
            headers=_HEADERS,
            transport=transport,
        )

    async def __aenter__(self) -> "GitHubGistClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    def _token(self) -> str:
        if self.settings.github_token is None or not self.settings.github_token.get_secret_value().strip():
            raise ConfigurationError("Set GITHUB_TOKEN in the root .env file.")
        return self.settings.github_token.get_secret_value().strip()

    def _resolve_gist_id(self, gist_id: str | None) -> str:
        """Explicit ID wins; otherwise fall back to GITHUB_GIST_ID in the root .env."""
        gist_id = gist_id or self.settings.github_gist_id
        if not gist_id or not gist_id.strip():
            raise ConfigurationError(
                "No Gist ID given and GITHUB_GIST_ID is not set in the root .env file."
            )
        return gist_id.strip()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self._token()}"},
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise RequestError("GitHub Gist request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"GitHub Gist request failed ({type(exc).__name__}).") from exc

        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"GitHub rejected the token (HTTP {response.status_code})."
            )
        if response.status_code >= 400:
            raise RequestError(
                f"GitHub Gist returned HTTP {response.status_code}.",
                status_code=response.status_code,
            )
        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponseParsingError("GitHub Gist response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ResponseParsingError("GitHub Gist response was not a JSON object.")
        return payload

    async def get(self, gist_id: str | None = None) -> Gist:
        gist_id = self._resolve_gist_id(gist_id)
        return self._parse_gist(await self._request("GET", f"/gists/{gist_id}"))

    async def create(
        self,
        files: Mapping[str, str],
        *,
        description: str = "",
        public: bool = False,
    ) -> Gist:
        if not files:
            raise ValueError("At least one file is required to create a gist.")
        payload = {
            "description": description,
            "public": public,
            "files": {name: {"content": content} for name, content in files.items()},
        }
        return self._parse_gist(await self._request("POST", "/gists", json=payload))

    async def update(
        self,
        gist_id: str | None,
        files: Mapping[str, str | None],
        *,
        description: str | None = None,
        public: bool | None = None,
    ) -> Gist:
        gist_id = self._resolve_gist_id(gist_id)
        payload: dict[str, Any] = {
            "files": {
                name: None if content is None else {"content": content}
                for name, content in files.items()
            }
        }
        if description is not None:
            payload["description"] = description
        if public is not None:
            payload["public"] = public
        return self._parse_gist(
            await self._request("PATCH", f"/gists/{gist_id}", json=payload)
        )

    async def delete_file(self, gist_id: str | None, filename: str) -> Gist:
        return await self.update(gist_id, {filename: None})

    async def delete(self, gist_id: str | None = None) -> None:
        gist_id = self._resolve_gist_id(gist_id)
        await self._request("DELETE", f"/gists/{gist_id}")

    @staticmethod
    def _parse_gist(payload: dict[str, Any]) -> Gist:
        try:
            return Gist.model_validate(payload)
        except Exception as exc:
            raise ResponseParsingError("Unexpected GitHub Gist response shape.") from exc
