"""Validated subset of GitHub GraphQL responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GitHubTagger(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str | None = None


class GitHubRefTarget(BaseModel):
    """A ref target: a ``Commit``, an annotated ``Tag``, a ``Tree`` or a ``Blob``.

    Only the fields this module asks for are modelled. ``oid`` and
    ``committedDate`` are set for commits; ``tagger`` and ``target`` for tags.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    typename: str = Field(alias="__typename")
    oid: str | None = None
    committedDate: str | None = None
    tagger: GitHubTagger | None = None
    target: GitHubRefTarget | None = None


class GitHubTagRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    target: GitHubRefTarget | None = None


class GitHubPageInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hasNextPage: bool
    endCursor: str | None = None


class GitHubTagRefConnection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pageInfo: GitHubPageInfo
    nodes: list[GitHubTagRef]


class GitHubTagRefsRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    refs: GitHubTagRefConnection


class GitHubTagRefsData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repository: GitHubTagRefsRepository | None = None


class GitHubTagRefsResponse(BaseModel):
    """``{"data": {"repository": ...}}`` for the tag list query."""

    model_config = ConfigDict(extra="ignore")

    data: GitHubTagRefsData
