"""Codeforces adapter: coordinates client, queries, response models and normalizer."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import TypeAdapter, ValidationError

from app.config import Settings, get_settings
from app.models.common import (
    AuthStatus,
    CollectionStatus,
    FetchMetadata,
    PlatformData,
    PlatformStatistics,
)
from app.scrapers.codeforces import normalizer, queries
from app.scrapers.codeforces.client import CodeforcesClient
from app.scrapers.codeforces.exceptions import (
    CodeforcesError,
    ConfigurationError,
    ResponseParsingError,
    UserNotFoundError,
)
from app.scrapers.codeforces.models import CFRatingChange, CFSubmission, CFUser

T = TypeVar("T")

_USERS = TypeAdapter(list[CFUser])
_SUBMISSIONS = TypeAdapter(list[CFSubmission])
_RATING_CHANGES = TypeAdapter(list[CFRatingChange])


@dataclass
class SubmissionsFetch:
    """Result of paginating the submission history."""

    submissions: list[CFSubmission]
    complete: bool  # False => `submissions` is only part of the history
    warnings: list[str] = field(default_factory=list)


class CodeforcesScraper:
    platform = "codeforces"

    def __init__(
        self,
        client: CodeforcesClient | None = None,
        settings: Settings | None = None,
        *,
        page_size: int = 2000,
        max_pages: int = 100,
        recent_limit: int = 20,
        include_gym: bool = True,
    ) -> None:
        self._settings = settings or (client.settings if client else None) or get_settings()
        self._owns_client = client is None
        self._client = client or CodeforcesClient(self._settings)
        self._page_size = page_size
        self._max_pages = max_pages
        self._recent_limit = recent_limit
        self._include_gym = include_gym

    async def __aenter__(self) -> "CodeforcesScraper":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    # ---- helpers ----------------------------------------------------------------
    @staticmethod
    def _validate(adapter: TypeAdapter[T], result: object, what: str) -> T:
        try:
            return adapter.validate_python(result)
        except ValidationError as exc:
            raise ResponseParsingError(f"Unexpected Codeforces response shape for {what}.") from exc

    # ---- fetchers ---------------------------------------------------------------
    async def fetch_user(self, handle: str) -> CFUser:
        result = await self._client.execute(queries.USER_INFO, queries.user_info_params(handle))
        users = self._validate(_USERS, result, "user info")
        if not users:
            raise UserNotFoundError(f"Codeforces user '{handle}' was not found.")
        return users[0]

    async def fetch_rating_history(self, handle: str) -> list[CFRatingChange]:
        result = await self._client.execute(queries.USER_RATING, queries.user_rating_params(handle))
        return self._validate(_RATING_CHANGES, result, "rating history")

    async def _fetch_submission_page(self, handle: str, start: int) -> list[CFSubmission]:
        result = await self._client.execute(
            queries.USER_STATUS, queries.user_status_params(handle, start, self._page_size)
        )
        return self._validate(_SUBMISSIONS, result, "submissions")

    async def fetch_submissions(self, handle: str) -> SubmissionsFetch:
        """Paginate the full submission history (newest first).

        Raises if the first page fails. If a later page fails, returns what was
        retrieved with complete=False. The history only counts as complete once an
        empty page comes back: Codeforces reports no total, and a short page could
        just mean the server capped the page size.
        """
        collected: list[CFSubmission] = []
        seen: set[int] = set()
        warnings: list[str] = []
        start = 1
        pages = 0
        complete = False

        while True:
            if pages >= self._max_pages:
                warnings.append(f"Stopped after {pages} pages (safety limit).")
                break
            try:
                page = await self._fetch_submission_page(handle, start)
            except CodeforcesError as exc:
                if pages == 0:
                    raise
                warnings.append(f"Fetching submissions at offset {start} failed: {exc}")
                break
            pages += 1

            if not page:
                complete = True
                break

            # New submissions arriving mid-pagination shift offsets and repeat items.
            new_items = 0
            for item in page:
                if item.id not in seen:
                    seen.add(item.id)
                    collected.append(item)
                    new_items += 1
            if new_items == 0:
                warnings.append("Codeforces returned a page with no new submissions; stopped.")
                break
            start += len(page)

        if not complete:
            warnings.append(
                f"Submission history is INCOMPLETE ({len(collected)} submissions retrieved)."
            )
        return SubmissionsFetch(collected, complete, list(dict.fromkeys(warnings)))

    # ---- orchestration ----------------------------------------------------------
    async def fetch_all(self, username: str | None = None) -> PlatformData:
        username = username or self._settings.codeforces_username
        if not username:
            raise ConfigurationError(
                "No Codeforces username given and CODEFORCES_USERNAME is not set."
            )

        # Public API: no credentials involved.
        meta = FetchMetadata(auth_status=AuthStatus.NOT_REQUIRED)

        def note_failure(section: str, exc: CodeforcesError) -> None:
            meta.failed_sections.append(section)
            meta.warnings.append(f"{section} unavailable: {exc}")

        # Sequential on purpose: the client throttles to Codeforces' request limit.
        canonical = username
        user: CFUser | None = None
        try:
            user = await self.fetch_user(username)
        except UserNotFoundError:
            raise
        except CodeforcesError as exc:
            note_failure("profile", exc)
        else:
            canonical = user.handle

        submissions: list[CFSubmission] | None = None
        try:
            fetched = await self.fetch_submissions(canonical)
        except CodeforcesError as exc:
            note_failure("submissions", exc)
        else:
            meta.warnings.extend(fetched.warnings)
            if self._include_gym:
                submissions = fetched.submissions
            else:
                submissions, gym_dropped = normalizer.split_gym(fetched.submissions)
                if gym_dropped:
                    meta.warnings.append(f"Ignored {gym_dropped} gym submissions.")
            if fetched.complete:
                meta.solved_problems_status = CollectionStatus.COMPLETE
            elif fetched.submissions:
                meta.solved_problems_status = CollectionStatus.PARTIAL

        rating_changes: list[CFRatingChange] | None = None
        try:
            rating_changes = await self.fetch_rating_history(canonical)
        except CodeforcesError as exc:
            note_failure("contest_rating", exc)

        contest = normalizer.normalize_contest(user, rating_changes)
        statistics = (
            normalizer.normalize_statistics(submissions, contest)
            if submissions is not None
            else PlatformStatistics(contest=contest)
        )

        return PlatformData(
            platform=self.platform,
            username=canonical,
            profile_url=normalizer.build_profile_url(canonical),
            fetched_at=datetime.now(timezone.utc),
            statistics=statistics,
            solved_problems=(
                normalizer.normalize_solved_problems(submissions) if submissions is not None else []
            ),
            recent_submissions=(
                normalizer.normalize_submissions(submissions, self._recent_limit)
                if submissions is not None
                else []
            ),
            activity=normalizer.normalize_calendar(submissions) if submissions is not None else None,
            metadata=meta,
        )