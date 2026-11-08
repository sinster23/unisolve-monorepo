"""Models mirroring Codeforces API responses (not the common models).

Each endpoint returns {"status": "OK", "result": ...}; the client unwraps the
envelope, so these models describe the items inside `result`.
"""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _none_to_list(value: Any) -> Any:
    return [] if value is None else value


class _CF(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# ---- user.info ---------------------------------------------------------------
class CFUser(_CF):
    """Rating fields are absent for users who never took part in a rated contest."""

    handle: str
    rating: int | None = None
    max_rating: int | None = Field(None, alias="maxRating")
    rank: str | None = None
    max_rank: str | None = Field(None, alias="maxRank")


# ---- user.status -------------------------------------------------------------
class CFProblem(_CF):
    contest_id: int | None = Field(None, alias="contestId")
    problemset_name: str | None = Field(None, alias="problemsetName")
    index: str
    name: str
    type: str | None = None
    points: float | None = None
    rating: int | None = None  # absent for unrated problems
    tags: Annotated[list[str], BeforeValidator(_none_to_list)] = Field(default_factory=list)


class CFSubmission(_CF):
    id: int
    contest_id: int | None = Field(None, alias="contestId")
    creation_time_seconds: int = Field(alias="creationTimeSeconds")
    problem: CFProblem
    programming_language: str | None = Field(None, alias="programmingLanguage")
    verdict: str | None = None  # absent while the submission is still being judged


# ---- user.rating -------------------------------------------------------------
class CFRatingChange(_CF):
    contest_id: int = Field(alias="contestId")
    contest_name: str | None = Field(None, alias="contestName")
    rank: int | None = None
    rating_update_time_seconds: int | None = Field(None, alias="ratingUpdateTimeSeconds")
    old_rating: int | None = Field(None, alias="oldRating")
    new_rating: int | None = Field(None, alias="newRating")