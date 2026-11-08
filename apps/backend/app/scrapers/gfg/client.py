"""HTTP transport for GeeksforGeeks (httpx only, Vercel friendly). No business logic.

* One shared `httpx.AsyncClient` with a connection pool sized to the concurrency limit.
* An `asyncio.Semaphore` caps in-flight requests so hundreds of per-problem calls
  can be launched with `asyncio.gather` without hammering GFG.
* Transient failures (transport errors, 429, 5xx) are retried with short backoff.
* Auth: either a raw cookie string (GFG_COOKIE) or a login with GFG_USERNAME /
  GFG_PASSWORD, done once per scraper run (serverless functions are stateless).

Messages must never contain the password or cookie values.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.scrapers.gfg.exceptions import (
    AuthenticationError,
    ConfigurationError,
    RateLimitedError,
    RequestError,
    ResponseParsingError,
)

HOME_URL = "https://www.geeksforgeeks.org/"
LOGIN_URL = "https://authapi.geeksforgeeks.org/api-post/sign-in/"

UA = (
    "Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
)
BASE_HEADERS = {"user-agent": UA, "accept-language": "en-US,en;q=0.9"}
API_HEADERS = {
    **BASE_HEADERS,
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.geeksforgeeks.org",
    "referer": "https://www.geeksforgeeks.org/",
}
LOGIN_HEADERS = {
    **API_HEADERS,
    "accept": "*/*",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
HTML_HEADERS = {**BASE_HEADERS, "accept": "text/html,*/*;q=0.8"}

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_TRIES = 3
DEFAULT_CONCURRENCY = 16
DEFAULT_UTC_OFFSET_MINUTES = 330  # GFG's "subtime" strings are IST


# ---- configuration -------------------------------------------------------------
def _setting(settings: Settings, name: str) -> str | None:
    """Read a GFG setting from Settings if the field exists, else from the environment.

    The env fallback means the adapter works on Vercel (real env vars) even if
    the gfg_* fields have not been added to app/config.py yet.
    """
    value: Any = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name.upper())
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    value = str(value).strip()
    return value or None


@dataclass(frozen=True)
class GFGConfig:
    cookie: str | None
    username: str | None
    password: str | None
    concurrency: int
    timeout: float
    tz: timezone

    @property
    def has_credentials(self) -> bool:
        """Cookie, or username+password. Says nothing about validity."""
        return bool(self.cookie or (self.username and self.password))

    @classmethod
    def from_settings(cls, settings: Settings) -> "GFGConfig":
        def _int(name: str, default: int) -> int:
            try:
                return int(_setting(settings, name) or default)
            except ValueError:
                return default

        return cls(
            cookie=_setting(settings, "gfg_cookie"),
            username=_setting(settings, "gfg_username"),
            password=_setting(settings, "gfg_password"),
            concurrency=max(1, _int("gfg_concurrency", DEFAULT_CONCURRENCY)),
            timeout=float(getattr(settings, "request_timeout", 15.0) or 15.0),
            tz=timezone(timedelta(minutes=_int("gfg_utc_offset_minutes", DEFAULT_UTC_OFFSET_MINUTES))),
        )


def parse_cookie_string(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def build_login_form(user: str, password: str) -> dict[str, str]:
    return {
        "reqType": "Login",
        "user": user,
        "pass": password,
        "g-recaptcha-response": "",
        "rem": "on",
        "to": HOME_URL,
        "server_type": "web",
    }


# ---- client --------------------------------------------------------------------
class GFGClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.config = GFGConfig.from_settings(self.settings)
        n = self.config.concurrency
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=n, max_keepalive_connections=n),
            transport=transport,
        )
        self._sem = asyncio.Semaphore(n)
        self._cookies: dict[str, str] = {}

    async def __aenter__(self) -> "GFGClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ---- auth -------------------------------------------------------------------
    async def login(self) -> None:
        """Establish cookies: GFG_COOKIE if set, otherwise a username/password login."""
        cfg = self.config
        if cfg.cookie:
            self._cookies = parse_cookie_string(cfg.cookie)
            return
        if not (cfg.username and cfg.password):
            raise ConfigurationError(
                "This request needs GFG credentials. Set GFG_COOKIE, or GFG_USERNAME "
                "and GFG_PASSWORD."
            )
        self._cookies = await self._login_with_password(cfg.username, cfg.password)

    async def _login_with_password(self, user: str, password: str) -> dict[str, str]:
        try:
            r = await self._send("GET", HOME_URL, headers=BASE_HEADERS, authenticated=False)
            self._raise_for_status(r, "GFG home page")
            headers = dict(LOGIN_HEADERS)
            csrf = self._http.cookies.get("csrftoken")
            if csrf:
                headers["x-csrftoken"] = csrf

            # Multipart form without filenames, as the browser sends it.
            files = {k: (None, v) for k, v in build_login_form(user, password).items()}
            r = await self._send(
                "POST", LOGIN_URL, headers=headers, files=files, authenticated=False
            )
            cookies = {c.name: c.value for c in self._http.cookies.jar}
        finally:
            self._http.cookies.clear()  # session lives in self._cookies only

        if r.status_code in (401, 403) or (r.status_code == 200 and "gfguserName" not in cookies):
            raise AuthenticationError("GFG rejected the login; check GFG_USERNAME and GFG_PASSWORD.")
        self._raise_for_status(r, "GFG login")
        if "gfguserName" not in cookies:
            raise AuthenticationError("GFG login did not return a session cookie.")
        return cookies

    # ---- requests ---------------------------------------------------------------
    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        files: Any = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        """One request with bounded concurrency and retry on transient failures."""
        hdrs = dict(headers or API_HEADERS)
        if authenticated and self._cookies:
            hdrs["cookie"] = self._cookie_header()

        last_exc: Exception | None = None
        for attempt in range(1, MAX_TRIES + 1):
            delay = 0.5 * attempt
            try:
                async with self._sem:
                    resp = await self._http.request(
                        method, url, headers=hdrs, json=json, files=files
                    )
            except httpx.TimeoutException as exc:
                last_exc = RequestError("GFG request timed out.")
                last_exc.__cause__ = exc
            except httpx.TransportError as exc:
                last_exc = RequestError(f"GFG request failed ({type(exc).__name__}).")
                last_exc.__cause__ = exc
            else:
                if resp.status_code not in RETRY_STATUSES or attempt == MAX_TRIES:
                    return resp
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    delay = min(float(retry_after), 5.0)
                last_exc = None
            if attempt < MAX_TRIES:
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code == 429:
            raise RateLimitedError(f"GFG rate limit reached (HTTP 429) for {what}.", status_code=code)
        if code in (401, 403):
            raise AuthenticationError(
                f"GFG returned HTTP {code} for {what}; cookies invalid/expired or request blocked."
            )
        raise RequestError(f"GFG returned HTTP {code} for {what}.", status_code=code)

    async def get_json(self, url: str, what: str) -> Any:
        resp = await self._send("GET", url)
        self._raise_for_status(resp, what)
        try:
            return resp.json()
        except ValueError as exc:
            raise ResponseParsingError(f"GFG {what} did not return JSON.") from exc

    async def post_json(self, url: str, payload: dict, what: str) -> Any:
        resp = await self._send("POST", url, json=payload)
        self._raise_for_status(resp, what)
        try:
            return resp.json()
        except ValueError as exc:
            raise ResponseParsingError(f"GFG {what} did not return JSON.") from exc

    async def get_text(self, url: str, what: str) -> str:
        resp = await self._send("GET", url, headers=HTML_HEADERS)
        self._raise_for_status(resp, what)
        return resp.text
