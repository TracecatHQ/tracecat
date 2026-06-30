"""Typed payloads for the GitLab REST API."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class GitLabListCommitsParams(TypedDict):
    """Query params for listing commits on a branch."""

    ref_name: str


class GitLabTreeParams(TypedDict):
    """Query params for listing a repository tree."""

    recursive: str
    ref: str


class GitLabCreateBranchParams(TypedDict):
    """Query params for creating a branch."""

    branch: str
    ref: str


class GitLabListMergeRequestsParams(TypedDict):
    """Query params for finding open merge requests between two branches."""

    state: Literal["opened"]
    source_branch: str
    target_branch: str


class GitLabCreateMergeRequestPayload(TypedDict):
    """JSON body for creating a merge request."""

    source_branch: str
    target_branch: str
    title: str
    description: str


GitLabCompareParams = TypedDict("GitLabCompareParams", {"from": str, "to": str})
"""Query params for comparing two refs (``from`` is a Python keyword)."""


class GitLabCommitAction(TypedDict):
    """A single file action in a GitLab commit-actions payload."""

    action: Literal["create", "update", "delete"]
    file_path: str
    content: NotRequired[str]


class GitLabCreateCommitPayload(TypedDict):
    """JSON body for creating a commit via the commit-actions API."""

    branch: str
    commit_message: str
    actions: list[GitLabCommitAction]


class GitLabCommit(BaseModel):
    """A GitLab commit, as returned by commit, compare, and create endpoints."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    short_id: str | None = None
    title: str | None = None
    message: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    authored_date: str | None = None
    committed_date: str | None = None
    created_at: str | None = None


class GitLabTreeEntry(BaseModel):
    """A single entry in a GitLab repository tree listing."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    type: str | None = None


class GitLabBranch(BaseModel):
    """A GitLab repository branch."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    default: bool = False


class GitLabMergeRequest(BaseModel):
    """A GitLab merge request."""

    model_config = ConfigDict(extra="ignore")

    iid: int
    web_url: str | None = None


class GitLabCompareResult(BaseModel):
    """A GitLab branch comparison."""

    model_config = ConfigDict(extra="ignore")

    commits: list[GitLabCommit] = Field(default_factory=list)
