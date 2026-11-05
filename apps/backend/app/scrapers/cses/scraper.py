"""CSES adapter: coordinates client, page routines, response models and normalizer."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.models.common import (
    AuthStatus,
    CollectionStatus,
    FetchMetadata,
    PlatformData,
    PlatformStatistics,
)
from app.scrapers.cses import normalizer, queries
from app.scrapers.cses.client import CSESClient
from app.scrapers.cses.exceptions import (
    AuthenticationError,
    ConfigurationError,
    CSESError,
    RequestError,
    ResponseParsingError,
)
from app.scrapers.cses.models import ProblemsetResponse


@dataclass
class ProblemsetFetch:
    """Result of fetching the problem set and the per-task solve details."""

    problemset: ProblemsetResponse
    details_complete: bool  # False => solved tasks have no solve/submission details
    warnings: list[str] = field(default_factory=list)


class CSESScraper:
    platform = "cses"

    def __init__(self, client: CSESClient | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or (client.settings if client else None) or get_settings()
        self._owns_client = client is None
        self._client = client or CSESClient(self._settings)

    async def __aenter__(self) -> "CSESScraper":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    # ---- fetchers ---------------------------------------------------------------
    async def fetch_problemset(self, username: str) -> ProblemsetFetch:
        """Login, fetch the problem set once, then the solve details in parallel.

        Raises if the problem set itself cannot be fetched. If only the solve
        details fail, returns the problem set with details_complete=False.
        """
        await self._client.login(username)
        http = self._client.http
        warnings: list[str] = []

        try:
            raw = await queries.fetch_problemset(http)
        except httpx.TimeoutException as exc:
            raise RequestError("CSES request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"CSES request failed ({type(exc).__name__}).") from exc

        if raw["total"] == 0:
            # A rejected session or changed markup both end up here.
            raise ResponseParsingError(
                "No tasks were parsed from the CSES problem set; the session may have "
                "been rejected or the page layout changed."
            )

        details_complete = True
        try:
            await queries.fetch_solved_details(http, raw["solved"])
        except httpx.HTTPError as exc:
            details_complete = False
            warnings.append(f"Solve dates unavailable: {type(exc).__name__}.")

        try:
            problemset = ProblemsetResponse.model_validate(raw)
        except ValidationError as exc:
            raise ResponseParsingError("Unexpected CSES problem set shape.") from exc
        return ProblemsetFetch(problemset, details_complete, warnings)

    # ---- orchestration ----------------------------------------------------------
    async def fetch_all(self, username: str | None = None) -> PlatformData:
        username = username or self._settings.cses_username
        if not username:
            raise ConfigurationError("No CSES username given and CSES_USERNAME is not set.")

        meta = FetchMetadata()
        statistics = PlatformStatistics()
        solved = []
        submissions = []
        profile_url = None

        if not self._settings.has_cses_credentials:
            meta.auth_status = AuthStatus.NOT_CONFIGURED
            meta.warnings.append(
                "No CSES credentials configured: solved_problems is empty, not complete."
            )
        else:
            try:
                fetched = await self.fetch_problemset(username)
            except AuthenticationError as exc:
                meta.auth_status = AuthStatus.FAILED
                meta.warnings.append(f"solved_problems unavailable: {exc}")
            except CSESError as exc:
                meta.failed_sections.append("problemset")
                meta.warnings.append(f"problemset unavailable: {exc}")
            else:
                stats = fetched.problemset
                meta.auth_status = AuthStatus.AUTHENTICATED
                meta.solved_problems_expected = len(stats.solved)
                meta.solved_problems_status = CollectionStatus.COMPLETE
                meta.warnings.extend(fetched.warnings)
                if not fetched.details_complete:
                    meta.failed_sections.append("solve_dates")

                profile_url = normalizer.build_profile_url(stats.profile_href)
                statistics = normalizer.normalize_statistics(stats)
                solved = normalizer.normalize_solved_problems(stats.solved)
                # recent_submissions = the latest submission (any status) of each solved task.
                submissions = normalizer.normalize_latest_submissions(stats.solved)

        return PlatformData(
            platform=self.platform,
            username=username,
            profile_url=profile_url,
            fetched_at=datetime.now(timezone.utc),
            statistics=statistics,
            solved_problems=solved,
            recent_submissions=submissions,
            activity=None,
            metadata=meta,
        )