from typing import Protocol

from app.models.common import PlatformData


class BaseScraper(Protocol):
    """Contract every platform adapter implements."""

    platform: str

    async def fetch_all(self, username: str | None = None) -> PlatformData:
        """Fetch everything for `username` and return normalized data."""
        ...