"""Converts Codeforces-specific models into the common models. No I/O."""

from collections import defaultdict
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
from app.scrapers.codeforces.models import CFProblem, CFRatingChange, CFSubmission, CFUser

CODEFORCES_BASE_URL = "https://codeforces.com"

# Contest ids from 100000 up are gym contests, not rated or practice contests.
GYM_MIN_CONTEST_ID = 100000

# Problem rating -> common Difficulty. Codeforces has no easy/medium/hard, so these
# thresholds are an interpretation; unrated problems stay UNKNOWN.
EASY_MAX_RATING = 1200
MEDIUM_MAX_RATING = 1900

# Verdicts whose display name is not simply the title-cased enum value.
_VERDICT_NAMES = {"OK": "Accepted"}
ACCEPTED_VERDICT = "OK"


# ---- helpers -------------------------------------------------------------------
def unix_to_datetime(value: int | str | None) -> datetime | None:
    """Unix seconds -> timezone-aware UTC datetime; None if missing/invalid."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def build_profile_url(handle: str) -> str:
    return f"{CODEFORCES_BASE_URL}/profile/{quote(handle, safe='')}"


def contest_id_of(submission: CFSubmission) -> int | None:
    problem_contest = submission.problem.contest_id
    return problem_contest if problem_contest is not None else submission.contest_id


def is_gym(submission: CFSubmission) -> bool:
    contest_id = contest_id_of(submission)
    return contest_id is not None and contest_id >= GYM_MIN_CONTEST_ID


def split_gym(submissions: Iterable[CFSubmission]) -> tuple[list[CFSubmission], int]:
    """(non-gym submissions, number of gym submissions dropped)."""
    kept: list[CFSubmission] = []
    dropped = 0
    for submission in submissions:
        if is_gym(submission):
            dropped += 1
        else:
            kept.append(submission)
    return kept, dropped


def build_problem_id(contest_id: int | None, problem: CFProblem) -> str | None:
    """e.g. "1900A". None when the problem has no contest."""
    return None if contest_id is None else f"{contest_id}{problem.index}"


def build_problem_url(contest_id: int | None, index: str | None) -> str | None:
    if contest_id is None or not index or not index.strip():
        return None
    kind = "gym" if contest_id >= GYM_MIN_CONTEST_ID else "contest"
    return f"{CODEFORCES_BASE_URL}/{kind}/{contest_id}/problem/{quote(index.strip(), safe='')}"


def normalize_difficulty(rating: int | None) -> Difficulty:
    if rating is None:
        return Difficulty.UNKNOWN
    if rating <= EASY_MAX_RATING:
        return Difficulty.EASY
    if rating <= MEDIUM_MAX_RATING:
        return Difficulty.MEDIUM
    return Difficulty.HARD


def normalize_verdict(verdict: str | None) -> str:
    if not verdict or not verdict.strip():
        return "Unknown"  # still being judged
    raw = verdict.strip()
    return _VERDICT_NAMES.get(raw, raw.replace("_", " ").title())


def clean_tags(tags: Iterable[str]) -> list[str]:
    """Deduplicated tags without Codeforces' internal "*"-prefixed markers (e.g. "*broken")."""
    cleaned = (t.strip() for t in tags)
    return list(dict.fromkeys(t for t in cleaned if t and not t.startswith("*")))

def _problem_key(submission: CFSubmission) -> str:
    problem_id = build_problem_id(contest_id_of(submission), submission.problem)
    return problem_id or f"{submission.problem.problemset_name or ''}:{submission.problem.name}"


def _accepted_by_problem(
    submissions: Iterable[CFSubmission],
) -> dict[str, list[CFSubmission]]:
    grouped: dict[str, list[CFSubmission]] = defaultdict(list)
    for submission in submissions:
        if submission.verdict == ACCEPTED_VERDICT:
            grouped[_problem_key(submission)].append(submission)
    return grouped


# ---- solved problems -----------------------------------------------------------
def normalize_solved_problems(submissions: Iterable[CFSubmission]) -> list[SolvedProblem]:
    """One entry per problem with at least one Accepted submission, latest solve first.

    first/last_submitted_at are the times of the oldest and newest Accepted
    submissions of that problem.
    """
    solved: list[SolvedProblem] = []
    for accepted in _accepted_by_problem(submissions).values():
        times = [s.creation_time_seconds for s in accepted]
        problem = accepted[0].problem
        contest_id = contest_id_of(accepted[0])
        solved.append(
            SolvedProblem(
                problem_id=build_problem_id(contest_id, problem),
                title=problem.name,
                problem_url=build_problem_url(contest_id, problem.index),
                difficulty=normalize_difficulty(problem.rating),
                first_submitted_at=unix_to_datetime(min(times)),
                last_submitted_at=unix_to_datetime(max(times)),
                topics=clean_tags(problem.tags),
            )
        )
    solved.sort(key=lambda p: p.last_submitted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return solved


# ---- submissions ---------------------------------------------------------------
def normalize_submission(submission: CFSubmission) -> Submission:
    contest_id = contest_id_of(submission)
    problem = submission.problem
    return Submission(
        submission_id=str(submission.id),
        problem_id=build_problem_id(contest_id, problem),
        problem_title=problem.name,
        problem_url=build_problem_url(contest_id, problem.index),
        submitted_at=unix_to_datetime(submission.creation_time_seconds),
        status=normalize_verdict(submission.verdict),
        language=submission.programming_language or None,
    )


def normalize_submissions(
    submissions: Iterable[CFSubmission], limit: int | None = None
) -> list[Submission]:
    """Newest first. Failed attempts are kept."""
    ordered = sorted(submissions, key=lambda s: (s.creation_time_seconds, s.id), reverse=True)
    if limit is not None:
        ordered = ordered[:limit]
    return [normalize_submission(s) for s in ordered]


# ---- statistics ----------------------------------------------------------------
def normalize_contest(
    user: CFUser | None, rating_changes: list[CFRatingChange] | None
) -> ContestStatistics | None:
    """None when neither source was available. An unrated user gets rating=None."""
    if user is None and rating_changes is None:
        return None
    return ContestStatistics(
        rating=user.rating if user else None,
        max_rating=user.max_rating if user else None,
        rank_title=user.rank if user else None,
        contests_attended=len(rating_changes) if rating_changes is not None else None,
    )


def normalize_statistics(
    submissions: list[CFSubmission] | None,
    contest: ContestStatistics | None = None,
) -> PlatformStatistics:
    """Statistics derived from the submission list.

    `submissions=None` means they could not be fetched: the counts then stay None
    (never 0). An empty list is a real answer: the user has no submissions.
    """
    if submissions is None:
        return PlatformStatistics(contest=contest)

    accepted = _accepted_by_problem(submissions)
    by_difficulty: dict[Difficulty, int] = defaultdict(int)
    topic_stats: dict[str, int] = defaultdict(int)
    language_problems: dict[str, set[str]] = defaultdict(set)

    for key, subs in accepted.items():
        problem = subs[0].problem
        by_difficulty[normalize_difficulty(problem.rating)] += 1
        for tag in clean_tags(problem.tags):
            topic_stats[tag] += 1
        for sub in subs:
            if sub.programming_language:
                language_problems[sub.programming_language].add(key)

    return PlatformStatistics(
        total_solved=len(accepted),
        easy_solved=by_difficulty[Difficulty.EASY],
        medium_solved=by_difficulty[Difficulty.MEDIUM],
        hard_solved=by_difficulty[Difficulty.HARD],
        total_submissions=len(submissions),
        accepted_submissions=sum(1 for s in submissions if s.verdict == ACCEPTED_VERDICT),
        language_stats={lang: len(keys) for lang, keys in language_problems.items()},
        topic_stats=dict(topic_stats),
        contest=contest,
    )


# ---- activity ------------------------------------------------------------------
def _current_streak(active_days: set[date], today: date) -> int:
    """Consecutive active UTC days ending today, or ending yesterday if today has
    no activity yet (so the streak isn't zeroed before the user has had a chance)."""
    day = today if today in active_days else today - timedelta(days=1)
    streak = 0
    while day in active_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def normalize_calendar(
    submissions: Iterable[CFSubmission], today: date | None = None
) -> ActivityCalendar:
    """Submissions (any verdict) per UTC day over the whole history."""
    daily: dict[date, int] = {}
    for submission in submissions:
        moment = unix_to_datetime(submission.creation_time_seconds)
        if moment is not None:
            daily[moment.date()] = daily.get(moment.date(), 0) + 1
    today = today or datetime.now(timezone.utc).date()
    return ActivityCalendar(
        active_years=sorted({day.year for day in daily}),
        current_streak=_current_streak(set(daily), today),
        total_active_days=len(daily),
        daily_counts=dict(sorted(daily.items())),
    )