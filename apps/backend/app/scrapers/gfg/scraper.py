"""GFG adapter: coordinates client, queries, response models and normalizer.

Request flow (everything that can run concurrently does):

    login (once)
      -> getUserInfo | profile page | solved list      (concurrent when the handle is known)
      -> submissions of every solved problem            (all concurrent, semaphore-capped)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings, get_settings
from app.models.common import (
    AuthStatus,
    CollectionStatus,
    FetchMetadata,
    PlatformData,
    PlatformStatistics,
)
from app.scrapers.gfg import normalizer, queries
from app.scrapers.gfg.client import GFGClient
from app.scrapers.gfg.exceptions import (
    AuthenticationError,
    ConfigurationError,
    GFGError,
)
from app.scrapers.gfg.models import ListingEntry, ProblemSubmissions


@dataclass
class GFGFetch:
    """Raw result of one full fetch."""

    handle: str
    expected: int | None
    listing: dict[str, ListingEntry]
    list_ok: bool
    details: dict[str, ProblemSubmissions]
    failed_sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class GFGScraper:
    platform = "geeksforgeeks"

    def __init__(self, client: GFGClient | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or (client.settings if client else None) or get_settings()
        self._owns_client = client is None
        self._client = client or GFGClient(self._settings)

    async def __aenter__(self) -> "GFGScraper":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    # ---- fetchers ---------------------------------------------------------------
    async def _profile_parts(self, handle: str, slugs: list[str] | None):
        """Profile page + solved list for `handle`, concurrently. Never raises GFGError."""
        client = self._client

        async def expected():
            return await queries.fetch_expected_total(client, handle)

        async def listing():
            return await queries.fetch_solved_listing(client, handle)

        coros = [expected()] + ([listing()] if slugs is None else [])
        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, GFGError):
                raise r  # programming error, not a GFG failure
        return results

    async def fetch_profile(
        self, username: str | None = None, slugs: list[str] | None = None
    ) -> GFGFetch:
        """Login, fetch everything, return raw results.

        Raises on auth/config failures or if the user cannot be identified. Failures of
        the optional parts (profile total, solved list, single problems) are recorded in
        `failed_sections` / `warnings` instead.
        """
        client = self._client
        warnings: list[str] = []
        failed: list[str] = []

        await client.login()

        # 1) identify the user. If a handle was given (not an email), start the profile
        #    page and solved list at the same time as getUserInfo.
        hint = username if username and "@" not in username else None
        parts_task = None
        if hint:
            parts_task = asyncio.create_task(self._profile_parts(hint, slugs))
        try:
            info = await queries.fetch_user_info(client)
        except BaseException:
            if parts_task:
                parts_task.cancel()
                await asyncio.gather(parts_task, return_exceptions=True)
            raise
        handle = info["handle"]

        if parts_task and hint.lower() == handle.lower():
            parts = await parts_task
        else:
            if parts_task:
                parts_task.cancel()
                await asyncio.gather(parts_task, return_exceptions=True)
                warnings.append(f"given username {hint!r} differs from logged-in handle {handle!r}")
            parts = await self._profile_parts(handle, slugs)

        # 2) interpret the concurrent results
        expected: int | None = None
        exp = parts[0]
        if isinstance(exp, BaseException):
            warnings.append(f"profile page fetch failed: {exp}")
        else:
            expected = exp
        if expected is None and not isinstance(exp, BaseException):
            warnings.append("could not read total_problems_solved from profile page")

        listing: dict[str, ListingEntry] = {}
        list_ok = False
        if slugs is not None:
            listing = {s.strip(): ListingEntry() for s in slugs if s.strip()}
            list_ok = bool(listing)
        else:
            res = parts[1]
            if isinstance(res, BaseException):
                warnings.append(f"solved list request failed: {res}")
            else:
                listing, count = res
                list_ok = bool(listing)
                if expected is None and count is not None:
                    expected = count  # fallback if the profile page had no count
        if not list_ok:
            failed.append("solved_problem_list")
            warnings.append("solved list unavailable; pass slugs= or check the request in DevTools")

        # 3) every problem's submissions, all at once
        details, failures = await queries.fetch_all_problem_submissions(client, list(listing))
        for slug, msg in failures.items():
            failed.append(f"submissions:{slug}")
            warnings.append(f"{slug}: {msg}")

        return GFGFetch(handle, expected, listing, list_ok, details, failed, warnings)

    # ---- orchestration ----------------------------------------------------------
    async def fetch_all(
        self, username: str | None = None, *, slugs: list[str] | None = None
    ) -> PlatformData:
        """`username`: GFG handle (optional, speeds things up; the real handle always comes
        from getUserInfo). `slugs`: skip the solved-list endpoint and use these slugs."""
        cfg = self._client.config
        username = username or cfg.username
        meta = FetchMetadata()
        tz = cfg.tz

        if not cfg.has_credentials:
            if not username:
                raise ConfigurationError("No GFG username given and GFG_USERNAME is not set.")
            meta.auth_status = AuthStatus.NOT_CONFIGURED
            meta.warnings.append(
                "No GFG credentials configured: solved_problems is empty, not complete."
            )
            return PlatformData(
                platform=self.platform,
                username=username,
                fetched_at=datetime.now(timezone.utc),
                statistics=PlatformStatistics(),
                metadata=meta,
            )

        handle = username or "unknown"
        solved, submissions, activity, statistics = [], [], None, PlatformStatistics()
        profile_url = None

        try:
            fetched = await self.fetch_profile(username, slugs)
        except AuthenticationError as exc:
            meta.auth_status = AuthStatus.FAILED
            meta.warnings.append(f"solved_problems unavailable: {exc}")
        except GFGError as exc:
            meta.auth_status = AuthStatus.AUTHENTICATED  # login worked; a later step failed
            meta.failed_sections.append("profile")
            meta.warnings.append(f"profile unavailable: {exc}")
        else:
            handle = fetched.handle
            profile_url = normalizer.build_profile_url(handle)
            meta.auth_status = AuthStatus.AUTHENTICATED
            meta.solved_problems_expected = fetched.expected
            meta.failed_sections.extend(fetched.failed_sections)
            meta.warnings.extend(fetched.warnings)

            solved, submissions, norm_warnings = normalizer.normalize_problems(
                fetched.details, fetched.listing, tz
            )
            meta.warnings.extend(norm_warnings)
            statistics = normalizer.normalize_statistics(solved, submissions)
            activity = normalizer.normalize_activity(submissions, tz)

            mismatch = fetched.expected is not None and len(solved) != fetched.expected
            if not fetched.list_ok and not fetched.details:
                meta.solved_problems_status = CollectionStatus.UNAVAILABLE
            elif fetched.failed_sections or mismatch:
                meta.solved_problems_status = CollectionStatus.PARTIAL
                if mismatch:
                    meta.warnings.append(
                        f"collected {len(solved)} solved problems but profile says {fetched.expected}"
                    )
            else:
                meta.solved_problems_status = CollectionStatus.COMPLETE

        return PlatformData(
            platform=self.platform,
            username=handle,
            profile_url=profile_url,
            fetched_at=datetime.now(timezone.utc),
            statistics=statistics,
            solved_problems=solved,
            recent_submissions=submissions,
            activity=activity,
            metadata=meta,
        )
