"""Offline self-check for the GitHub Gist client.

Uses httpx.MockTransport so no real GitHub call is made; GITHUB_TOKEN and
GITHUB_GIST_ID are supplied via Settings instead of the real environment.
Run from apps/backend:   python -m tests.github_gist.test_client
"""

import asyncio
import sys

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.tools.github_gist.client import GitHubGistClient
from app.tools.github_gist.exceptions import (
    AuthenticationError,
    ConfigurationError,
    RequestError,
)

# Mirrors GITHUB_GIST_ID in the root .env.example. The tests construct Settings
# with this value instead of reading the real environment.
GIST_ID = "f0e66a8164f508c2c25e2dc157a7fa6d"


def settings(token: str | None = "test-token", gist_id: str | None = GIST_ID) -> Settings:
    return Settings(
        github_token=SecretStr(token) if token else None,
        github_gist_id=gist_id,
    )


def gist_payload() -> dict:
    return {
        "id": "abc123",
        "description": "demo",
        "public": False,
        "html_url": "https://gist.github.com/abc123",
        "files": {
            "hello.txt": {"filename": "hello.txt", "content": "hello", "size": 5},
        },
    }


async def test_create_sends_auth_and_file_payload() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=gist_payload())

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.create({"hello.txt": "hello"}, description="demo")

    assert result.id == "abc123"
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/gists"
    assert requests[0].read().decode() == (
        '{"description":"demo","public":false,"files":{"hello.txt":{"content":"hello"}}}'
    )


async def test_update_null_removes_a_file() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == f"/gists/{GIST_ID}"
        assert request.read().decode() == '{"files":{"hello.txt":null}}'
        return httpx.Response(200, json=gist_payload())

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(handler)) as client:
        # gist_id omitted: the client falls back to GITHUB_GIST_ID from settings
        result = await client.delete_file(None, "hello.txt")

    assert result.id == "abc123"


async def test_delete_gist_accepts_empty_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == f"/gists/{GIST_ID}"
        return httpx.Response(204)

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(handler)) as client:
        await client.delete()


async def test_missing_token_fails_without_making_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    async with GitHubGistClient(settings(None), transport=httpx.MockTransport(handler)) as client:
        try:
            await client.get("abc123")
        except ConfigurationError:
            return
        raise AssertionError("expected ConfigurationError")


async def test_missing_env_gist_id_fails_without_making_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    async with GitHubGistClient(settings(gist_id=None), transport=httpx.MockTransport(handler)) as client:
        for call in (client.get(), client.update(None, {}), client.delete_file(None, "f.txt"), client.delete()):
            try:
                await call
            except ConfigurationError:
                continue
            raise AssertionError("expected ConfigurationError")


async def test_authentication_and_http_errors_are_translated() -> None:
    async def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(auth_handler)) as client:
        try:
            await client.get("abc123")
        except AuthenticationError:
            pass
        else:
            raise AssertionError("expected AuthenticationError")

    async def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(error_handler)) as client:
        try:
            await client.get("missing")
        except RequestError as error:
            assert error.status_code == 404, error.status_code
        else:
            raise AssertionError("expected RequestError")


async def test_get_parses_gist() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/gists/{GIST_ID}"
        return httpx.Response(200, json=gist_payload())

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(handler)) as client:
        # gist_id omitted: the client falls back to GITHUB_GIST_ID from settings
        gist = await client.get()

    assert gist.files["hello.txt"].content == "hello"
    assert gist.public is False


async def test_explicit_gist_id_overrides_env() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gists/override-id"
        return httpx.Response(200, json=gist_payload())

    async with GitHubGistClient(settings(), transport=httpx.MockTransport(handler)) as client:
        await client.get("override-id")


TESTS = [
    test_create_sends_auth_and_file_payload,
    test_update_null_removes_a_file,
    test_delete_gist_accepts_empty_response,
    test_missing_token_fails_without_making_request,
    test_missing_env_gist_id_fails_without_making_request,
    test_authentication_and_http_errors_are_translated,
    test_get_parses_gist,
    test_explicit_gist_id_overrides_env,
]


async def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            await test()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc}")
        else:
            print(f"[PASS] {test.__name__}")
    if failed:
        print(f"\n{failed} test(s) failed.")
        return 1
    print(f"\nAll {len(TESTS)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
