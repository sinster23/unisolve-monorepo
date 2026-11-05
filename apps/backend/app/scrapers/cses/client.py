"""HTTP transport for CSES. Contains no business logic.

`extract_csrf_token`, `login_cses` and `fetch_url` are the original scraping
routines, copied unchanged. `CSESClient` only wraps them: it runs the login,
holds the authenticated httpx session, and maps failures to adapter exceptions.
Messages must never contain the password or the PHPSESSID value.
"""

import asyncio
import re

import httpx

from app.config import Settings, get_settings
from app.scrapers.cses.exceptions import (
    AuthenticationError,
    ConfigurationError,
    RateLimitedError,
    RequestError,
    ResponseParsingError,
)

CSES_BASE_URL = "https://cses.fi"
REQUEST_TIMEOUT = 30  # same value the original scraper used for both clients


# ---- original scraping routines (unchanged) ---------------------------------------
def extract_csrf_token(html):
    """
    Extract the CSRF token from CSES login page HTML.

    Returns:
        str: The CSRF token.

    Raises:
        ValueError: If the token is not found in the HTML.
    """
    match = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        html
    )
    if match:
        return match.group(1)
    else:
        raise ValueError("CSRF token not found in page HTML")


def login_cses(username, password):
    """
    Login to CSES (synchronous, run once before the async section)
    and return the PHPSESSID session token.

    Returns:
        tuple: (success_bool, phpsessid)
    """
    # Use a short-lived sync client just for the login
    client = httpx.Client(timeout=30)

    # Step 1: Get the login page and extract the token
    resp = client.get("https://cses.fi/login/")
    resp.raise_for_status()

    csrf_token = extract_csrf_token(resp.text)

    # Step 2: Submit the login form
    payload = {
        "csrf_token": csrf_token,
        "nick": username,
        "pass": password,
    }

    login_resp = client.post(
        "https://cses.fi/login/",
        data=payload,
        headers={"Referer": "https://cses.fi/login/"},
    )

    # Step 3: Verify login success
    # A successful login returns a 302 redirect to "/" (dashboard)
    # A failed login returns 200 with "Invalid username or password"
    success = any(
        r.status_code == 302 and r.headers.get("location", "") == "/"
        for r in login_resp.history
    ) or login_resp.status_code == 302

    phpsessid = client.cookies.get("PHPSESSID")

    client.close()

    return success, phpsessid


async def fetch_url(client, url):
    # Asynchronously send the request, freeing up the loop for other requests
    response = await client.get(url)
    return response.text


# ---- adapter transport ------------------------------------------------------------
class CSESClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport)

    async def __aenter__(self) -> "CSESClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        """The shared async session (carries PHPSESSID after `login`)."""
        return self._http

    async def login(self, username: str) -> None:
        """Log in once and attach PHPSESSID to the shared async session."""
        if not self.settings.has_cses_credentials:
            raise ConfigurationError(
                "This request needs CSES credentials. Set CSES_USERNAME and "
                "CSES_PASSWORD in the root .env file."
            )
        password = self.settings.cses_password.get_secret_value()  # type: ignore[union-attr]

        try:
            # login_cses is synchronous; keep it off the event loop.
            success, phpsessid = await asyncio.to_thread(login_cses, username, password)
        except httpx.TimeoutException as exc:
            raise RequestError("CSES login timed out.") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise RateLimitedError("CSES rate limit reached (HTTP 429).", status_code=status) from exc
            raise RequestError(f"CSES returned HTTP {status} during login.", status_code=status) from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"CSES login failed ({type(exc).__name__}).") from exc
        except ValueError as exc:
            raise ResponseParsingError("CSES login page did not contain a CSRF token.") from exc

        if not success or not phpsessid:
            raise AuthenticationError(
                "CSES rejected the login; check CSES_USERNAME and CSES_PASSWORD."
            )

        self._http.cookies.set("PHPSESSID", phpsessid, domain="cses.fi")
