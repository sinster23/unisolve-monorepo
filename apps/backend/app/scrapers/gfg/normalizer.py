"""Converts GFG-specific models into the common models. No I/O."""

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from app.models.common import (
    ActivityCalendar,
    Difficulty,
    PlatformStatistics,
    SolvedProblem,
    Submission,
)
from app.scrapers.gfg.models import ListingEntry, ProblemSubmissions, RawSubmission

GFG_BASE_URL = "https://www.geeksforgeeks.org"


def build_profile_url(handle: str) -> str:
    return f"{GFG_BASE_URL}/profile/{quote(handle, safe='')}"


def build_problem_url(slug: str) -> str:
    return f"{GFG_BASE_URL}/problems/{quote(slug, safe='')}/0"


def parse_time(s: str | None, tz: timezone) -> datetime | None:
    """GFG's naive 'YYYY-MM-DD HH:MM:SS' (in `tz`) -> aware UTC datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def is_correct(sub: RawSubmission) -> bool:
    return str(sub.exec_status) == "1" or sub.exec_status_text == "Correct"


def map_difficulty(text: str | None) -> Difficulty:
    t = (text or "").strip().lower()
    if t in ("school", "basic", "easy"):  # GFG has 5 levels; School/Basic fold into EASY
        return Difficulty.EASY
    if t == "medium":
        return Difficulty.MEDIUM
    if t == "hard":
        return Difficulty.HARD
    return Difficulty.UNKNOWN


def title_from_slug(slug: str) -> str:
    base = re.sub(r"-?\d+$", "", slug)  # drop GFG's numeric suffix
    return base.replace("-", " ").title()


def compute_streak(days: set[date], today: date) -> int:
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


# ---- problems + submissions ----------------------------------------------------
def normalize_problems(
    details: Mapping[str, ProblemSubmissions],
    listing: Mapping[str, ListingEntry],
    tz: timezone,
    *,
    title_from_slug_only: bool = False,
) -> tuple[list[SolvedProblem], list[Submission], list[str]]:
    """Returns (solved problems, all submissions newest first, warnings)."""
    solved: list[SolvedProblem] = []
    submissions: list[Submission] = []
    warnings: list[str] = []

    for slug, res in details.items():
        # The slug is the key we requested: it is the identity we emit.
        if res.slug and res.slug != slug:
            warnings.append(f"{slug}: API returned a different slug ({res.slug}); using {slug}")

        entry = listing.get(slug)
        if title_from_slug_only:
            title = title_from_slug(slug)
        else:
            title = res.problem_name or (entry.title if entry else None) or title_from_slug(slug)
        url = build_problem_url(slug)

        diff = map_difficulty(res.problem_level_text)
        if diff == Difficulty.UNKNOWN and entry:
            diff = map_difficulty(entry.bucket)

        ok_times: list[datetime] = []
        for i, sub in enumerate(res.submissions):
            when = parse_time(sub.subtime, tz)
            correct = is_correct(sub)
            if correct and when:
                ok_times.append(when)
            sid = sub.submission_id
            submissions.append(
                Submission(
                    submission_id=str(sid) if sid is not None else f"gfg-{slug}-{i}",
                    problem_id=slug,
                    problem_title=title,
                    problem_url=url,
                    submitted_at=when,
                    status=sub.exec_status_text or ("Correct" if correct else "Wrong"),
                    language=sub.lang_display_name or sub.lang,
                )
            )

        if not ok_times:
            warnings.append(f"{slug}: listed as solved but no correct submission found")
        solved.append(
            SolvedProblem(
                problem_id=slug,
                title=title,
                problem_url=url,
                difficulty=diff,
                first_submitted_at=min(ok_times) if ok_times else None,
                last_submitted_at=max(ok_times) if ok_times else None,
                topics=[],  # GFG does not return topics from these endpoints
            )
        )

    submissions.sort(
        key=lambda s: s.submitted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True
    )
    return solved, submissions, warnings


# ---- statistics / activity -----------------------------------------------------
def normalize_statistics(
    solved: list[SolvedProblem], submissions: list[Submission]
) -> PlatformStatistics:
    lang_stats: dict[str, int] = {}
    for s in submissions:
        if s.language:
            lang_stats[s.language] = lang_stats.get(s.language, 0) + 1
    return PlatformStatistics(
        total_solved=len(solved),
        easy_solved=sum(p.difficulty == Difficulty.EASY for p in solved),
        medium_solved=sum(p.difficulty == Difficulty.MEDIUM for p in solved),
        hard_solved=sum(p.difficulty == Difficulty.HARD for p in solved),
        total_submissions=len(submissions),
        accepted_submissions=sum(s.status == "Correct" for s in submissions),
        language_stats=lang_stats,
    )


def normalize_activity(
    submissions: list[Submission], tz: timezone, now: datetime | None = None
) -> ActivityCalendar:
    """Daily submission counts, bucketed by day in GFG's timezone."""
    daily: dict[date, int] = {}
    for s in submissions:
        if s.submitted_at:
            d = s.submitted_at.astimezone(tz).date()
            daily[d] = daily.get(d, 0) + 1
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    return ActivityCalendar(
        active_years=sorted({d.year for d in daily}),
        current_streak=compute_streak(set(daily), today),
        total_active_days=len(daily),
        daily_counts=daily,
    )
