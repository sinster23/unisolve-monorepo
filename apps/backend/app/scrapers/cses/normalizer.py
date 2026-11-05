"""Converts CSES-specific models into the common models. No I/O."""

from collections.abc import Iterable
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from app.models.common import Difficulty, PlatformStatistics, SolvedProblem, Submission
from app.scrapers.cses.models import ProblemsetResponse, Task

CSES_BASE_URL = "https://cses.fi"


def build_profile_url(href: str | None) -> str | None:
    """Absolute profile URL from the (usually relative) href of the "account" link."""
    if not href or not href.strip():
        return None
    return urljoin(f"{CSES_BASE_URL}/", href.strip())


def build_problem_url(task_id: str | None) -> str | None:
    if not task_id or not task_id.strip() or task_id.strip() == "?":
        return None
    return f"{CSES_BASE_URL}/problemset/task/{quote(task_id.strip(), safe='')}"


def unix_to_datetime(value: int | str | None) -> datetime | None:
    """Unix seconds -> timezone-aware UTC datetime; None if missing/invalid."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


# ---- statistics ----------------------------------------------------------------
def normalize_statistics(stats: ProblemsetResponse) -> PlatformStatistics:
    """Only what CSES shows: the solved count. Everything else stays None (never 0)."""
    return PlatformStatistics(total_solved=len(stats.solved))


# ---- solved problems -----------------------------------------------------------
def normalize_solved_problem(task: Task) -> SolvedProblem:
    # CSES has no difficulty or topic tags. first/last_submitted_at are the times
    # of the oldest and newest Accepted submissions of the task, both taken from
    # the same walk of its submission list (they are equal when solved only once).
    # They stay None if the solve details could not be fetched.
    return SolvedProblem(
        problem_id=task.id,
        title=task.name,
        problem_url=build_problem_url(task.id),
        difficulty=Difficulty.UNKNOWN,
        first_submitted_at=(
            unix_to_datetime(task.oldest_solve.unix_time) if task.oldest_solve else None
        ),
        last_submitted_at=(
            unix_to_datetime(task.newest_solve.unix_time) if task.newest_solve else None
        ),
        topics=[],
    )


def normalize_solved_problems(tasks: Iterable[Task]) -> list[SolvedProblem]:
    """Each problem appears once, in problem-set order."""
    unique: dict[str, Task] = {}
    for task in tasks:
        unique.setdefault(task.id if task.id != "?" else task.name, task)
    return [normalize_solved_problem(t) for t in unique.values()]


# ---- submissions ---------------------------------------------------------------
def normalize_oldest_solves(tasks: Iterable[Task]) -> list[Submission]:
    """One Accepted submission per solved task (its oldest), newest first."""
    with_solve = [t for t in tasks if t.oldest_solve is not None]
    with_solve.sort(key=lambda t: t.oldest_solve.unix_time, reverse=True)  # type: ignore[union-attr]
    return [
        Submission(
            # The view page exposes no submission id, so build a stable synthetic one.
            submission_id=f"cses-{t.id}-{t.oldest_solve.unix_time}",  # type: ignore[union-attr]
            problem_id=t.id,
            problem_title=t.name,
            problem_url=build_problem_url(t.id),
            submitted_at=unix_to_datetime(t.oldest_solve.unix_time),  # type: ignore[union-attr]
            status="Accepted",
            language=t.oldest_solve.language or None,  # type: ignore[union-attr]
        )
        for t in with_solve
    ]


def normalize_latest_submissions(tasks: Iterable[Task]) -> list[Submission]:
    """One submission per task (its latest, Accepted or not), newest first."""
    with_latest = [t for t in tasks if t.latest_submission is not None]
    with_latest.sort(key=lambda t: t.latest_submission.unix_time, reverse=True)  # type: ignore[union-attr]
    return [
        Submission(
            # The view page exposes no submission id, so build a stable synthetic one.
            submission_id=f"cses-{t.id}-{t.latest_submission.unix_time}",  # type: ignore[union-attr]
            problem_id=t.id,
            problem_title=t.name,
            problem_url=build_problem_url(t.id),
            submitted_at=unix_to_datetime(t.latest_submission.unix_time),  # type: ignore[union-attr]
            status=t.latest_submission.status,  # type: ignore[union-attr]
            language=t.latest_submission.language or None,  # type: ignore[union-attr]
        )
        for t in with_latest
    ]