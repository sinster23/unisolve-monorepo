"""Models for GitHub's Gist REST API."""

from pydantic import BaseModel, ConfigDict


class _GitHub(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GistFile(_GitHub):
    filename: str
    content: str | None = None
    raw_url: str | None = None
    size: int | None = None
    truncated: bool = False


class Gist(_GitHub):
    id: str
    description: str | None = None
    public: bool
    html_url: str
    files: dict[str, GistFile]
