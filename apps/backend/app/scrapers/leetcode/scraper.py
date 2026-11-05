"""LeetCode adapter: coordinates client, queries, response models and normalizer."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.models.common import (
    AuthStatus,
    CollectionStatus,
    FetchMetadata,
    PlatformData,
    PlatformStatistics,
)
from app.scrapers.leetcode import normalizer, queries
from app.scrapers.leetcode.client import LeetCodeClient
from app.scrapers.leetcode.exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    LeetCodeError,
    ResponseParsingError,
    SessionOwnerMismatchError,
    UserNotFoundError,
)
from app.scrapers.leetcode.models import (
    CalendarResponse,
    MatchedUser,
    ProfileResponse,
    RecentAcceptedResponse,
    RecentSubmission,
    RecentSubmissionsResponse,
    SolvedProblemsPage,
    SolvedProblemsResponse,
    SolvedQuestion,
    UserCalendar,
    UserStatusResponse,
)

M = TypeVar("M", bound=BaseModel)

PROGRESS_REFERER = "https://leetcode.com/progress/"


@dataclass
class SolvedProblemsFetch:
    """Result of paginating the solved-problem list."""

    questions: list[SolvedQuestion]
    total_reported: int | None
    complete: bool  # False => `questions` is only part of the history
    warnings: list[str] = field(default_factory=list)


class LeetCodeScraper:
    platform = "leetcode"

    def __init__(
        self,
        client: LeetCodeClient | None = None,
        settings: Settings | None = None,
        *,
        page_size: int = 100,
        page_delay: float = 0.5,
        max_pages: int = 100,
    ) -> None:
        self._settings = settings or (client.settings if client else None) or get_settings()
        self._owns_client = client is None
        self._client = client or LeetCodeClient(self._settings)
        self._page_size = page_size
        self._page_delay = page_delay
        self._max_pages = max_pages

    async def __aenter__(self) -> "LeetCodeScraper":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    # ---- helpers ----------------------------------------------------------------
    async def _query(
        self,
        query: str,
        model: type[M],
        variables: dict | None = None,
        *,
        what: str,
        authenticated: bool = False,
        referer: str | None = None,
    ) -> M:
        data = await self._client.execute(query, variables, authenticated, referer)
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise ResponseParsingError(f"Unexpected LeetCode response shape for {what}.") from exc

    @staticmethod
    def _raise_if_user_missing(exc: APIError) -> None:
        if any("does not exist" in m.lower() for m in exc.messages):
            raise UserNotFoundError("LeetCode user was not found.") from exc

    # ---- public fetchers --------------------------------------------------------
    async def fetch_profile(self, username: str) -> MatchedUser:
        try:
            response = await self._query(
                queries.PROFILE_QUERY, ProfileResponse, {"username": username}, what="profile"
            )
        except APIError as exc:
            self._raise_if_user_missing(exc)
            raise
        if response.matched_user is None:
            raise UserNotFoundError(f"LeetCode user '{username}' was not found.")
        return response.matched_user

    async def fetch_statistics(self, username: str) -> PlatformStatistics:
        return normalizer.normalize_statistics(await self.fetch_profile(username))

    async def fetch_recent_submissions(
        self, username: str, limit: int = 20
    ) -> list[RecentSubmission]:
        response = await self._query(
            queries.RECENT_SUBMISSIONS_QUERY,
            RecentSubmissionsResponse,
            {"username": username, "limit": limit},
            what="recent submissions",
        )
        return response.recent_submission_list

    async def fetch_recent_accepted(self, username: str, limit: int = 20) -> list[RecentSubmission]:
        response = await self._query(
            queries.RECENT_ACCEPTED_QUERY,
            RecentAcceptedResponse,
            {"username": username, "limit": limit},
            what="recent accepted submissions",
        )
        return response.recent_ac_submission_list

    async def fetch_calendar(self, username: str, year: int | None = None) -> UserCalendar:
        year = year or datetime.now(timezone.utc).year
        try:
            response = await self._query(
                queries.CALENDAR_QUERY,
                CalendarResponse,
                {"username": username, "year": year},
                what="calendar",
            )
        except APIError as exc:
            self._raise_if_user_missing(exc)
            raise
        if response.matched_user is None:
            raise UserNotFoundError(f"LeetCode user '{username}' was not found.")
        if response.matched_user.user_calendar is None:
            raise ResponseParsingError("LeetCode response had no userCalendar.")
        return response.matched_user.user_calendar

    # ---- authenticated: solved problems -----------------------------------------
    async def _verify_session_owner(self, username: str) -> None:
        """userProgressQuestionList has no username argument: it returns the data
        of whoever owns the session. Never label it with a different username."""
        response = await self._query(
            queries.USER_STATUS_QUERY,
            UserStatusResponse,
            what="session status",
            authenticated=True,
            referer=PROGRESS_REFERER,
        )
        status = response.user_status
        if status is None or not status.is_signed_in:
            raise AuthenticationError(
                "LeetCode reports the configured session is not signed in. "
                "Refresh LEETCODE_SESSION and LEETCODE_CSRF_TOKEN."
            )
        if (status.username or "").casefold() != username.casefold():
            raise SessionOwnerMismatchError(
                f"The configured session belongs to '{status.username}', not '{username}'. "
                "Solved problems can only be fetched for the session owner."
            )

    async def _fetch_solved_page(self, skip: int) -> SolvedProblemsPage:
        response = await self._query(
            queries.SOLVED_PROBLEMS_QUERY,
            SolvedProblemsResponse,
            {"filters": {"questionStatus": "SOLVED", "skip": skip, "limit": self._page_size}},
            what="solved problems",
            authenticated=True,
            referer=PROGRESS_REFERER,
        )
        if response.user_progress_question_list is None:
            raise AuthenticationError(
                "LeetCode returned no solved-problem data; the session is likely invalid or expired."
            )
        return response.user_progress_question_list

    async def fetch_solved_problems(self, username: str) -> SolvedProblemsFetch:
        """Paginate the full solved list.

        Raises if nothing could be fetched (auth/config/first-page errors). If a
        later page fails, returns what was retrieved with complete=False.
        """
        await self._verify_session_owner(username)

        collected: list[SolvedQuestion] = []
        seen: set[str] = set()
        warnings: list[str] = []
        total: int | None = None
        skip = 0
        pages = 0
        complete = False

        while True:
            if pages >= self._max_pages:
                warnings.append(f"Stopped after {pages} pages (safety limit).")
                break
            try:
                page = await self._fetch_solved_page(skip)
            except LeetCodeError as exc:
                if pages == 0:
                    raise
                warnings.append(f"Fetching solved problems at offset {skip} failed: {exc}")
                break
            pages += 1

            if total is not None and page.total_num != total:
                warnings.append(
                    f"totalNum changed during pagination ({total} -> {page.total_num})."
                )
            total = page.total_num

            new_items = 0
            for question in page.questions:
                key = question.title_slug
                if key in seen:
                    continue
                seen.add(key)
                collected.append(question)
                new_items += 1
            if new_items < len(page.questions):
                warnings.append("Duplicate problems were returned across pages and ignored.")

            skip += len(page.questions)

            if len(collected) >= total:
                complete = True
                break
            if not page.questions:
                warnings.append("LeetCode returned an empty page before all problems were fetched.")
                break
            if new_items == 0:
                warnings.append("LeetCode returned a page with no new problems; stopped.")
                break
            if self._page_delay:
                await asyncio.sleep(self._page_delay)

        if not complete:
            warnings.append(
                f"Solved-problem list is INCOMPLETE ({len(collected)} of {total} retrieved)."
            )
        return SolvedProblemsFetch(collected, total, complete, list(dict.fromkeys(warnings)))

    # ---- orchestration ----------------------------------------------------------
    async def fetch_all(self, username: str | None = None) -> PlatformData:
        username = username or self._settings.leetcode_username
        if not username:
            raise ConfigurationError(
                "No LeetCode username given and LEETCODE_USERNAME is not set."
            )

        meta = FetchMetadata()

        def note_failure(section: str, exc: LeetCodeError) -> None:
            meta.failed_sections.append(section)
            meta.warnings.append(f"{section} unavailable: {exc}")

        # Independent public queries run concurrently.
        profile_r, recent_r, calendar_r = await asyncio.gather(
            self.fetch_profile(username),
            self.fetch_recent_submissions(username),
            self.fetch_calendar(username),
            return_exceptions=True,
        )
        for result in (profile_r, recent_r, calendar_r):
            # Bugs and cancellation must not be turned into warnings.
            if isinstance(result, BaseException) and not isinstance(result, LeetCodeError):
                raise result
        if isinstance(profile_r, UserNotFoundError):
            raise profile_r

        canonical = username
        statistics = PlatformStatistics()
        if isinstance(profile_r, LeetCodeError):
            note_failure("profile", profile_r)
        else:
            canonical = profile_r.username
            statistics = normalizer.normalize_statistics(profile_r)

        submissions = []
        if isinstance(recent_r, LeetCodeError):
            note_failure("recent_submissions", recent_r)
        else:
            submissions = normalizer.normalize_submissions(recent_r)

        activity = None
        if isinstance(calendar_r, LeetCodeError):
            note_failure("calendar", calendar_r)
        else:
            activity = normalizer.normalize_calendar(calendar_r)

        # Authenticated solved-problem history (sequential pagination).
        solved = []
        if not self._settings.has_leetcode_credentials:
            meta.auth_status = AuthStatus.NOT_CONFIGURED
            meta.warnings.append(
                "No LeetCode credentials configured: solved_problems is empty, not complete."
            )
        else:
            try:
                fetched = await self.fetch_solved_problems(canonical)
            except AuthenticationError as exc:
                meta.auth_status = AuthStatus.FAILED
                meta.warnings.append(f"solved_problems unavailable: {exc}")
            except LeetCodeError as exc:
                meta.auth_status = AuthStatus.AUTHENTICATED
                meta.warnings.append(f"solved_problems unavailable: {exc}")
            else:
                meta.auth_status = AuthStatus.AUTHENTICATED
                meta.solved_problems_expected = fetched.total_reported
                meta.warnings.extend(fetched.warnings)
                if fetched.complete:
                    meta.solved_problems_status = CollectionStatus.COMPLETE
                elif fetched.questions:
                    meta.solved_problems_status = CollectionStatus.PARTIAL
                solved = normalizer.normalize_solved_problems(fetched.questions)
                if (
                    fetched.complete
                    and statistics.total_solved is not None
                    and len(solved) != statistics.total_solved
                ):
                    meta.warnings.append(
                        f"Fetched {len(solved)} solved problems but the profile reports "
                        f"{statistics.total_solved}."
                    )

        return PlatformData(
            platform=self.platform,
            username=canonical,
            profile_url=normalizer.build_profile_url(canonical),
            fetched_at=datetime.now(timezone.utc),
            statistics=statistics,
            solved_problems=solved,
            recent_submissions=submissions,
            activity=activity,
            metadata=meta,
        )