"""Converts LeetCode-specific models into the common models. No I/O."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from app.models.common import (
    ActivityCalendar,
    ContestStatistics,
    Difficulty,
    PlatformStatistics,
    SolvedProblem,
    Submission,
)
from app.scrapers.leetcode.models import (
    ContestResponse,
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
def _current_streak(active_days: set[date], today: date) -> int:
    """Consecutive active UTC days ending today, or ending yesterday if today has
    no activity yet (so the streak isn't zeroed before the user has had a chance).

    LeetCode's own `streak` field is not used: it is the longest run within the
    queried year, not the current streak.
    """
    day = today if today in active_days else today - timedelta(days=1)
    streak = 0
    while day in active_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def _calendar_days(calendar: UserCalendar) -> dict[date, int]:
    daily: dict[date, int] = {}
    for ts, count in calendar.submission_calendar.items():
        moment = unix_to_datetime(ts)
        if moment is not None:
            day = moment.date()
            daily[day] = daily.get(day, 0) + count
    return daily


def normalize_calendar(
    calendars: UserCalendar | Iterable[UserCalendar], today: date | None = None
) -> ActivityCalendar:
    """Merge one calendar per year into a single history.

    LeetCode returns one year per request, so pass every year's calendar. Days
    are keyed by date; a day repeated across calendars is not double counted.
    Streak and active-day totals are computed from the merged history.
    """
    if isinstance(calendars, UserCalendar):
        calendars = [calendars]

    daily: dict[date, int] = {}
    reported_years: set[int] = set()
    for calendar in calendars:
        reported_years.update(calendar.active_years)
        for day, count in _calendar_days(calendar).items():
            daily[day] = max(daily.get(day, 0), count)

    active_days = {day for day, count in daily.items() if count > 0}
    today = today or datetime.now(timezone.utc).date()
    return ActivityCalendar(
        active_years=sorted(reported_years | {day.year for day in active_days}),
        current_streak=_current_streak(active_days, today),
        total_active_days=len(active_days),
        daily_counts=dict(sorted(daily.items())),
    )


# ---- contests ------------------------------------------------------------------
def _round_rating(value: float | None) -> int | None:
    return None if value is None else round(value)


def normalize_contest(response: ContestResponse) -> ContestStatistics:
    """Contest standing from the contest query.

    A user who never entered a contest has no ranking: they get rating=None and
    contests_attended=0 (the query succeeded, so 0 is a real answer). LeetCode has
    no max-rating field, so it is derived from the attended history.
    """
    ranking = response.user_contest_ranking
    attended = [e for e in response.user_contest_ranking_history if e.attended]

    ratings = [e.rating for e in attended if e.rating is not None]
    if ranking is not None and ranking.rating is not None:
        ratings.append(ranking.rating)

    if ranking is not None and ranking.attended_contests_count is not None:
        contests_attended: int = ranking.attended_contests_count
    else:
        contests_attended = len(attended)

    badge = ranking.badge.name if ranking and ranking.badge and ranking.badge.name else None
    return ContestStatistics(
        rating=_round_rating(ranking.rating) if ranking else None,
        max_rating=_round_rating(max(ratings)) if ratings else None,
        rank_title=badge,
        contests_attended=contests_attended,
    )