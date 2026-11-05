"""CSES adapter exceptions. Messages must never contain passwords or session cookies."""


class CSESError(Exception):
    """Base class for all CSES adapter errors."""


class ConfigurationError(CSESError):
    """Required configuration (username, credentials) is missing."""


class AuthenticationError(CSESError):
    """CSES rejected the login or the session."""


class RequestError(CSESError):
    """Network failure, timeout or non-success HTTP status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(RequestError):
    """HTTP 429."""


class ResponseParsingError(CSESError):
    """The page was not the expected HTML or did not match the expected shape."""
