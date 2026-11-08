"""Models mirroring what the GeeksforGeeks endpoints return (not the common models).

They are deliberately tolerant (everything optional, extras ignored) because GFG's
APIs are undocumented and change shape without notice.
"""

from pydantic import BaseModel, ConfigDict, field_validator


class _GFG(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class RawSubmission(_GFG):
    """One entry of a problem's submission list."""

    submission_id: str | int | None = None
    subtime: str | None = None  # "%Y-%m-%d %H:%M:%S" in GFG's timezone (IST by default)
    exec_status: str | int | None = None  # 1 / "1" => correct
    exec_status_text: str | None = None  # "Correct", "Wrong Answer", ...
    lang: str | None = None
    lang_display_name: str | None = None


class ProblemSubmissions(_GFG):
    """`results` object of the per-problem submissions endpoint."""

    slug: str | None = None
    problem_name: str | None = None
    problem_level_text: str | None = None  # School / Basic / Easy / Medium / Hard
    submissions: list[RawSubmission] = []

    @field_validator("submissions", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return [] if v is None else v


class ListingEntry(_GFG):
    """One solved problem found in the solved-list response."""

    title: str | None = None
    bucket: str | None = None  # school / basic / easy / medium / hard
