"""Models mirroring LeetCode's GraphQL responses (not the common models)."""

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _none_to_list(value: Any) -> Any:
    return [] if value is None else value


def _int_to_str(value: Any) -> Any:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


class _LC(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


StrId = Annotated[str, BeforeValidator(_int_to_str)]


# ---- A. profile --------------------------------------------------------------
class UserProfileInfo(_LC):
    real_name: str | None = Field(None, alias="realName")
    country_name: str | None = Field(None, alias="countryName")
    ranking: int | None = None
    reputation: int | None = None
    about_me: str | None = Field(None, alias="aboutMe")


# ---- B. submission statistics ------------------------------------------------
class SubmissionCount(_LC):
    """`count` = distinct problems; `submissions` = total attempts."""

    difficulty: str
    count: int | None = None
    submissions: int | None = None


class SubmitStats(_LC):
    ac_submission_num: Annotated[list[SubmissionCount], BeforeValidator(_none_to_list)] = (
        Field(default_factory=list, alias="acSubmissionNum")
    )
    total_submission_num: Annotated[list[SubmissionCount], BeforeValidator(_none_to_list)] = (
        Field(default_factory=list, alias="totalSubmissionNum")
    )


# ---- C. language statistics --------------------------------------------------
class LanguageProblemCount(_LC):
    """Problems solved per language (not a count of all attempts)."""

    language_name: str = Field(alias="languageName")
    problems_solved: int = Field(alias="problemsSolved")


class TagProblemCount(_LC):
    tag_name: str = Field(alias="tagName")
    tag_slug: str | None = Field(None, alias="tagSlug")
    problems_solved: int = Field(alias="problemsSolved")


class TagProblemCounts(_LC):
    advanced: Annotated[list[TagProblemCount], BeforeValidator(_none_to_list)] = []
    intermediate: Annotated[list[TagProblemCount], BeforeValidator(_none_to_list)] = []
    fundamental: Annotated[list[TagProblemCount], BeforeValidator(_none_to_list)] = []


class MatchedUser(_LC):
    username: str
    profile: UserProfileInfo | None = None
    submit_stats: SubmitStats | None = Field(None, alias="submitStats")
    language_problem_count: Annotated[
        list[LanguageProblemCount], BeforeValidator(_none_to_list)
    ] = Field(default_factory=list, alias="languageProblemCount")
    tag_problem_counts: TagProblemCounts | None = Field(None, alias="tagProblemCounts")


class ProfileResponse(_LC):
    matched_user: MatchedUser | None = Field(None, alias="matchedUser")


# ---- D. solved problems ------------------------------------------------------
class TopicTag(_LC):
    name: str


class SolvedQuestion(_LC):
    frontend_id: StrId | None = Field(None, alias="frontendId")
    title: str
    title_slug: str = Field(alias="titleSlug")
    difficulty: str | None = None  # raw value; mapped by the normalizer
    last_submitted_at: datetime | None = Field(None, alias="lastSubmittedAt")
    topic_tags: Annotated[list[TopicTag], BeforeValidator(_none_to_list)] = Field(
        default_factory=list, alias="topicTags"
    )

    @field_validator("last_submitted_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class SolvedProblemsPage(_LC):
    total_num: int = Field(alias="totalNum")
    questions: list[SolvedQuestion]


class SolvedProblemsResponse(_LC):
    user_progress_question_list: SolvedProblemsPage | None = Field(
        None, alias="userProgressQuestionList"
    )


# ---- E. recent submissions ---------------------------------------------------
class RecentSubmission(_LC):
    id: StrId
    title: str
    title_slug: str | None = Field(None, alias="titleSlug")
    timestamp: int | None = None  # unix seconds, delivered as a string
    status_display: str | None = Field(None, alias="statusDisplay")
    lang: str | None = None


class RecentSubmissionsResponse(_LC):
    recent_submission_list: Annotated[
        list[RecentSubmission], BeforeValidator(_none_to_list)
    ] = Field(default_factory=list, alias="recentSubmissionList")


class RecentAcceptedResponse(_LC):
    recent_ac_submission_list: Annotated[
        list[RecentSubmission], BeforeValidator(_none_to_list)
    ] = Field(default_factory=list, alias="recentAcSubmissionList")


# ---- F. calendar -------------------------------------------------------------
class UserCalendar(_LC):
    active_years: Annotated[list[int], BeforeValidator(_none_to_list)] = Field(
        default_factory=list, alias="activeYears"
    )
    streak: int | None = None
    total_active_days: int | None = Field(None, alias="totalActiveDays")
    # unix timestamp (day start, UTC) -> number of submissions
    submission_calendar: dict[int, int] = Field(default_factory=dict, alias="submissionCalendar")

    @field_validator("submission_calendar", mode="before")
    @classmethod
    def _parse_json_string(cls, value: Any) -> Any:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("submissionCalendar is not valid JSON") from exc
        return value


class CalendarUser(_LC):
    user_calendar: UserCalendar | None = Field(None, alias="userCalendar")


class CalendarResponse(_LC):
    matched_user: CalendarUser | None = Field(None, alias="matchedUser")


# ---- G. contests -------------------------------------------------------------
class ContestBadge(_LC):
    name: str | None = None


class ContestRanking(_LC):
    """Current standing. The whole object is null for users who never entered a contest."""

    attended_contests_count: int | None = Field(None, alias="attendedContestsCount")
    rating: float | None = None  # delivered as a float, e.g. 1873.42
    global_ranking: int | None = Field(None, alias="globalRanking")
    total_participants: int | None = Field(None, alias="totalParticipants")
    top_percentage: float | None = Field(None, alias="topPercentage")
    badge: ContestBadge | None = None  # null until a badge is earned


class ContestInfo(_LC):
    title: str | None = None
    start_time: int | None = Field(None, alias="startTime")  # unix seconds


class ContestHistoryEntry(_LC):
    attended: bool = False  # False => registered but did not take part
    rating: float | None = None  # rating AFTER this contest
    ranking: int | None = None
    contest: ContestInfo | None = None


class ContestResponse(_LC):
    user_contest_ranking: ContestRanking | None = Field(None, alias="userContestRanking")
    user_contest_ranking_history: Annotated[
        list[ContestHistoryEntry], BeforeValidator(_none_to_list)
    ] = Field(default_factory=list, alias="userContestRankingHistory")


# ---- session check -----------------------------------------------------------
class UserStatus(_LC):
    is_signed_in: bool = Field(False, alias="isSignedIn")
    username: str | None = None


class UserStatusResponse(_LC):
    user_status: UserStatus | None = Field(None, alias="userStatus")