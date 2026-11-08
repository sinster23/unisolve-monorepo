"""Application settings, loaded from the monorepo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/backend/app/config.py -> repository root
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPOSITORY_ROOT / ".env"  # absolute: independent of the working directory


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # "LEETCODE_SESSION=" means "not configured"
        extra="ignore",  # .env also holds settings for other providers
    )

    leetcode_username: str | None = None
    leetcode_session: SecretStr | None = None
    leetcode_csrf_token: SecretStr | None = None
    cses_username: str | None = None
    cses_password: SecretStr | None = None
    codeforces_username: str | None = None
    # GeeksforGeeks: either a raw browser cookie string (gfg_cookie), or
    # gfg_username + gfg_password. The cookie wins when both are set.
    gfg_cookie: SecretStr | None = None
    gfg_username: str | None = None
    gfg_password: SecretStr | None = None
    gfg_concurrency: int = 16  # max in-flight requests to GFG
    gfg_utc_offset_minutes: int = 330  # GFG's "subtime" strings are IST (UTC+5:30)
    request_timeout: float = 15.0

    @property
    def has_leetcode_credentials(self) -> bool:
        """True when both values are present. Says nothing about validity:
        that is only known once an authenticated request is made."""
        return all(
            secret is not None and secret.get_secret_value().strip()
            for secret in (self.leetcode_session, self.leetcode_csrf_token)
        )

    @property
    def has_cses_credentials(self) -> bool:
        """True when username and password are both present. Says nothing about
        validity: that is only known once a login is attempted."""
        return bool(
            self.cses_username
            and self.cses_username.strip()
            and self.cses_password is not None
            and self.cses_password.get_secret_value().strip()
        )

    @property
    def has_gfg_credentials(self) -> bool:
        """True when a cookie, or both username and password, are present. Says
        nothing about validity: that is only known once a login is attempted."""
        has_cookie = (
            self.gfg_cookie is not None and bool(self.gfg_cookie.get_secret_value().strip())
        )
        has_login = bool(
            self.gfg_username
            and self.gfg_username.strip()
            and self.gfg_password is not None
            and self.gfg_password.get_secret_value().strip()
        )
        return has_cookie or has_login


@lru_cache
def get_settings() -> Settings:
    return Settings()