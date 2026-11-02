from typing import Protocol, TypeVar

ProfileT = TypeVar("ProfileT")


class BaseScraper(Protocol[ProfileT]):
    async def fetch_profile(self, username: str) -> ProfileT:
        """Fetch and return a validated, platform-specific profile model."""
        ...
