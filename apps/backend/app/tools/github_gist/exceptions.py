"""GitHub Gist adapter exceptions."""


class GitHubGistError(Exception):
    """Base class for GitHub Gist adapter errors."""


class ConfigurationError(GitHubGistError):
    """Required configuration is missing."""


class AuthenticationError(GitHubGistError):
    """GitHub rejected the configured token."""


class RequestError(GitHubGistError):
    """Network failure or non-success HTTP status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ResponseParsingError(GitHubGistError):
    """GitHub returned an unexpected response shape."""
