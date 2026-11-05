"""Models mirroring what the CSES scraper extracts (not the common models).

These validate the dicts produced by the routines in queries.py.
"""

from pydantic import BaseModel, ConfigDict


class _CSES(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AcceptedSolve(_CSES):
    """An Accepted submission of a task (from the task's submission list)."""

    time_str: str  # as shown on the page, "%Y-%m-%d %H:%M:%S"
    language: str
    unix_time: int


class LatestSubmission(_CSES):
    """The most recent submission of a task, whatever its result."""

    time_str: str  # as shown on the page, "%Y-%m-%d %H:%M:%S"
    language: str
    unix_time: int
    status: str  # "Accepted", "Not Accepted", "Partial", "Pending", ...


# Backwards-compatible name.
OldestSolve = AcceptedSolve


class Task(_CSES):
    id: str  # "?" if it could not be parsed
    name: str
    detail: str
    score: str  # CSS class suffix: full / zero / partial / ...
    oldest_solve: AcceptedSolve | None = None  # only set for solved tasks
    newest_solve: AcceptedSolve | None = None  # only set for solved tasks
    latest_submission: LatestSubmission | None = None  # newest row, any status


class ProblemsetResponse(_CSES):
    total: int
    solved: list[Task] = []
    attempted: list[Task] = []
    partial: list[Task] = []
    skipped: list[Task] = []
    pending: list[Task] = []
    compile_err: list[Task] = []
    not_attempted: list[Task] = []
    profile_href: str | None = None  # href of the "account" link, as found in the HTML