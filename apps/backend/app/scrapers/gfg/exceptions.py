"""GFG adapter exceptions. Messages must never contain passwords or session cookies."""


class GFGError(Exception):
    """Base class for all GFG adapter errors."""


class ConfigurationError(GFGError):
    """Required configuration (username, credentials) is missing."""


class AuthenticationError(GFGError):
    """GFG rejected the login or the session cookies."""


class RequestError(GFGError):
    """Network failure, timeout or non-success HTTP status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(RequestError):
    """HTTP 429 that persisted after retries."""


class ResponseParsingError(GFGError):
    """The response was not the expected JSON/HTML or did not match the expected shape."""
