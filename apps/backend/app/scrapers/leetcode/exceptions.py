"""LeetCode adapter exceptions. Messages must never contain cookie values."""


class LeetCodeError(Exception):
    """Base class for all LeetCode adapter errors."""


class ConfigurationError(LeetCodeError):
    """Required configuration (username, credentials) is missing."""


class AuthenticationError(LeetCodeError):
    """LeetCode rejected the configured session."""


class SessionOwnerMismatchError(AuthenticationError):
    """The session belongs to a different user than the one requested."""


class RequestError(LeetCodeError):
    """Network failure, timeout or non-success HTTP status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(RequestError):
    """HTTP 429."""


class APIError(LeetCodeError):
    """The GraphQL response contained errors or no data."""

    def __init__(self, message: str, messages: list[str] | None = None) -> None:
        super().__init__(message)
        self.messages = messages or []


class ResponseParsingError(LeetCodeError):
    """The response was not valid JSON or did not match the expected shape."""


class UserNotFoundError(LeetCodeError):
    """No LeetCode user with the requested username."""