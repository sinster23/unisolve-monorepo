"""Platform-independent models returned by every scraper adapter.

Nothing in this module may reference a specific platform's field names.
"""

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl, computed_field


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    UNKNOWN = "unknown"


class SolvedProblem(BaseModel):
    """One unique problem solved by the user (appears once per problem)."""

    problem_id: str | None = None
    title: str
    problem_url: HttpUrl | None = None
    difficulty: Difficulty = Difficulty.UNKNOWN
    # Last known submission time for this problem, NOT the time it was first solved.
    last_submitted_at: AwareDatetime | None = None
    topics: list[str] = Field(default_factory=list)


class Submission(BaseModel):
    """One submission attempt. Failed attempts are kept."""

    submission_id: str
    problem_id: str | None = None
    problem_title: str
    problem_url: HttpUrl | None = None
    submitted_at: AwareDatetime | None = None
    status: str
    language: str | None = None


class PlatformStatistics(BaseModel):
    """Aggregate statistics. None means the platform did not provide the value."""

    total_solved: int | None = None
    easy_solved: int | None = None
    medium_solved: int | None = None
    hard_solved: int | None = None
    total_submissions: int | None = None
    accepted_submissions: int | None = None
    language_stats: dict[str, int] = Field(default_factory=dict)
    topic_stats: dict[str, int] = Field(default_factory=dict)


class ActivityCalendar(BaseModel):
    """Daily activity counts (submissions, contributions, ...)."""

    active_years: list[int] = Field(default_factory=list)
    current_streak: int | None = None
    total_active_days: int | None = None
    daily_counts: dict[date, int] = Field(default_factory=dict)


class AuthStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


class CollectionStatus(StrEnum):
    COMPLETE = "complete"  # every page/record retrieved
    PARTIAL = "partial"  # some records retrieved, list is NOT the full history
    UNAVAILABLE = "unavailable"  # not attempted or nothing retrieved


class FetchMetadata(BaseModel):
    """Tells consumers whether the returned data is complete."""

    auth_status: AuthStatus = AuthStatus.NOT_CONFIGURED
    solved_problems_status: CollectionStatus = CollectionStatus.UNAVAILABLE
    solved_problems_expected: int | None = None
    failed_sections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        return (
            self.solved_problems_status == CollectionStatus.COMPLETE
            and not self.failed_sections
        )


class PlatformData(BaseModel):
    """Final normalized response returned by each scraper."""

    platform: str
    username: str
    profile_url: HttpUrl | None = None
    fetched_at: AwareDatetime
    statistics: PlatformStatistics = Field(default_factory=PlatformStatistics)
    solved_problems: list[SolvedProblem] = Field(default_factory=list)
    recent_submissions: list[Submission] = Field(default_factory=list)
    activity: ActivityCalendar | None = None
    metadata: FetchMetadata = Field(default_factory=FetchMetadata)