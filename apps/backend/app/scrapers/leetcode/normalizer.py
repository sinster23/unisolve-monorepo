"""Converts LeetCode-specific models into the common models. No I/O."""

from collections.abc import Iterable
from datetime import date, datetime, timezone
from urllib.parse import quote

from app.models.common import (
    ActivityCalendar,
    Difficulty,
    PlatformStatistics,
    SolvedProblem,
    Submission,
)
from app.scrapers.leetcode.models import (
    MatchedUser,
    RecentSubmission,
    SolvedQuestion,
    SubmissionCount,
    UserCalendar,
)

LEETCODE_BASE_URL = "https://leetcode.com"


def build_profile_url(username: str) -> str:
    return f"{LEETCODE_BASE_URL}/u/{quote(username, safe='')}/"


def build_problem_url(title_slug: str | None) -> str | None:
    if not title_slug or not title_slug.strip():
        return None
    return f"{LEETCODE_BASE_URL}/problems/{quote(title_slug.strip(), safe='-_')}/"


def normalize_difficulty(raw: str | None) -> Difficulty:
    try:
        return Difficulty((raw or "").strip().lower())
    except ValueError:
        return Difficulty.UNKNOWN


def unix_to_datetime(value: int | str | None) -> datetime | None:
    """Unix seconds -> timezone-aware UTC datetime; None if missing/invalid."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


# ---- A/D. profile + statistics -------------------------------------------------
def _by_difficulty(rows: Iterable[SubmissionCount]) -> dict[str, SubmissionCount]:
    return {row.difficulty.strip().lower(): row for row in rows}


def normalize_statistics(user: MatchedUser) -> PlatformStatistics:
    """Statistics from the profile query. Missing values stay None (never 0)."""
    stats = user.submit_stats
    ac = _by_difficulty(stats.ac_submission_num) if stats else {}
    total = _by_difficulty(stats.total_submission_num) if stats else {}

    # `count` = distinct problems, `submissions` = attempts.
    topic_stats: dict[str, int] = {}
    if user.tag_problem_counts:
        groups = user.tag_problem_counts
        for tag in (*groups.fundamental, *groups.intermediate, *groups.advanced):
            topic_stats[tag.tag_name] = tag.problems_solved

    return PlatformStatistics(
        total_solved=ac["all"].count if "all" in ac else None,
        easy_solved=ac["easy"].count if "easy" in ac else None,
        medium_solved=ac["medium"].count if "medium" in ac else None,
        hard_solved=ac["hard"].count if "hard" in ac else None,
        total_submissions=total["all"].submissions if "all" in total else None,
        accepted_submissions=ac["all"].submissions if "all" in ac else None,
        language_stats={
            item.language_name: item.problems_solved for item in user.language_problem_count
        },
        topic_stats=topic_stats,
    )


# ---- B. solved problems --------------------------------------------------------
def normalize_solved_problem(question: SolvedQuestion) -> SolvedProblem:
    topics = list(dict.fromkeys(t.name.strip() for t in question.topic_tags if t.name.strip()))
    return SolvedProblem(
        problem_id=question.frontend_id,
        title=question.title,
        problem_url=build_problem_url(question.title_slug),
        difficulty=normalize_difficulty(question.difficulty),
        last_submitted_at=question.last_submitted_at,
        topics=topics,
    )


def normalize_solved_problems(questions: Iterable[SolvedQuestion]) -> list[SolvedProblem]:
    """Each problem appears once; if repeated, the latest submission time wins."""
    unique: dict[str, SolvedQuestion] = {}
    for question in questions:
        key = question.title_slug or question.frontend_id or question.title
        existing = unique.get(key)
        if existing is None:
            unique[key] = question
        elif (question.last_submitted_at or datetime.min.replace(tzinfo=timezone.utc)) > (
            existing.last_submitted_at or datetime.min.replace(tzinfo=timezone.utc)
        ):
            unique[key] = question  # keeps the original position in the dict
    return [normalize_solved_problem(q) for q in unique.values()]


# ---- C. submissions ------------------------------------------------------------
def normalize_submission(item: RecentSubmission, default_status: str = "Unknown") -> Submission:
    # The recent-submission queries return no problem number, and a title slug is
    # not the same kind of identifier as a solved problem's number, so problem_id
    # stays None. Link submissions to solved problems through problem_url.
    return Submission(
        submission_id=item.id,
        problem_id=None,
        problem_title=item.title,
        problem_url=build_problem_url(item.title_slug),
        submitted_at=unix_to_datetime(item.timestamp),
        status=item.status_display or default_status,
        language=item.lang or None,
    )


def normalize_submissions(
    items: Iterable[RecentSubmission], default_status: str = "Unknown"
) -> list[Submission]:
    return [normalize_submission(item, default_status) for item in items]


# ---- calendar ------------------------------------------------------------------
def normalize_calendar(calendar: UserCalendar) -> ActivityCalendar:
    daily: dict[date, int] = {}
    for ts, count in calendar.submission_calendar.items():
        moment = unix_to_datetime(ts)
        if moment is not None:
            day = moment.date()
            daily[day] = daily.get(day, 0) + count
    return ActivityCalendar(
        active_years=sorted(calendar.active_years),
        current_streak=calendar.streak,
        total_active_days=calendar.total_active_days,
        daily_counts=dict(sorted(daily.items())),
    )