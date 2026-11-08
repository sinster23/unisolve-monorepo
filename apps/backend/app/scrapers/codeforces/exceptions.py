"""Codeforces adapter exceptions. The public API needs no credentials, so there is
no authentication error here."""


class CodeforcesError(Exception):
    """Base class for all Codeforces adapter errors."""


class ConfigurationError(CodeforcesError):
    """Required configuration (username) is missing."""


class RequestError(CodeforcesError):
    """Network failure, timeout or non-success HTTP status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(RequestError):
    """HTTP 429/503 or a "Call limit exceeded" reply."""


class APIError(CodeforcesError):
    """The reply had status FAILED (other than a missing user or rate limit)."""

    def __init__(self, message: str, comment: str | None = None) -> None:
        super().__init__(message)
        self.comment = comment


class ResponseParsingError(CodeforcesError):
    """The response was not valid JSON or did not match the expected shape."""


class UserNotFoundError(CodeforcesError):
    """No Codeforces user with the requested handle."""