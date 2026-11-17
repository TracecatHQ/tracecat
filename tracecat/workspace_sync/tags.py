"""Pure helpers for listing repository tags across VCS providers.

The transports own the HTTP calls; this module owns what is the same for every
provider: the GitHub GraphQL query, the opaque page cursor, and turning each
provider's tag payload into the commit it points at.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from github.GithubException import GithubException, RateLimitExceededException
from pydantic import BaseModel, ConfigDict, Field

from tracecat.exceptions import TracecatValidationError
from tracecat.vcs.bitbucket.types import BitbucketTag
from tracecat.vcs.github.types import GitHubRefTarget
from tracecat.vcs.gitlab.types import GitLabCommit, GitLabTag

TAG_PAGE_MAX = 100
"""Largest tag page requested from any provider in one call."""

GITHUB_TAG_PEEL_DEPTH = 3
"""Annotated tags followed per ref; GraphQL fragments cannot recurse."""


def _github_tag_target_fields(depth: int) -> str:
    """GraphQL selection for a ref target, peeling up to ``depth`` tags."""
    commit = "__typename ... on Commit { oid committedDate }"
    if depth == 0:
        return commit
    inner = _github_tag_target_fields(depth - 1)
    return f"{commit} ... on Tag {{ tagger {{ date }} target {{ {inner} }} }}"


GITHUB_LIST_TAGS_QUERY = f"""
query($owner: String!, $name: String!, $first: Int!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    refs(
      refPrefix: "refs/tags/"
      first: $first
      after: $after
      orderBy: {{field: TAG_COMMIT_DATE, direction: DESC}}
    ) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        name
        target {{ {_github_tag_target_fields(GITHUB_TAG_PEEL_DEPTH)} }}
      }}
    }}
  }}
}}
"""


@dataclass(frozen=True, slots=True)
class TagCommit:
    """The commit a tag points at, and the date to show for the tag."""

    sha: str
    date: str | None
    """The tag's own date when the provider has one, else the commit's."""


class _TagCursor(BaseModel):
    """Where the next tag page starts, tied to the page size that issued it.

    ``position`` is a page number for GitLab and Bitbucket Cloud and a start
    offset for Bitbucket Data Center.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(ge=0, le=2**31 - 1)
    page_size: int = Field(ge=1, le=TAG_PAGE_MAX)


def encode_tag_cursor(position: int, page_size: int) -> str:
    """Encode a tag page position as an opaque cursor."""
    payload = _TagCursor(position=position, page_size=page_size).model_dump_json()
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_tag_cursor(cursor: str | None, *, page_size: int, first: int) -> int:
    """Return the position ``cursor`` points at, or ``first`` without one.

    Args:
        cursor: A cursor from :func:`encode_tag_cursor`, or ``None``.
        page_size: The effective page size of this request.
        first: The first position: 1 for page numbers, 0 for offsets.

    Raises:
        TracecatValidationError: The cursor is malformed, or was issued for a
            different page size, so following it would skip or repeat tags.
    """
    if cursor is None:
        return first
    try:
        decoded = _TagCursor.model_validate_json(base64.urlsafe_b64decode(cursor))
    except ValueError as e:
        raise TracecatValidationError("Invalid tag cursor") from e
    if decoded.position < first:
        raise TracecatValidationError("Invalid tag cursor")
    if decoded.page_size != page_size:
        raise TracecatValidationError("Tag cursor was issued for a different page size")
    return decoded.position


def github_tag_commit(target: GitHubRefTarget | None) -> TagCommit | None:
    """Return the commit a GitHub tag ref points at.

    Annotated tags are peeled up to :data:`GITHUB_TAG_PEEL_DEPTH` levels and
    dated by the outermost tagger, falling back to the commit's committed date.
    Lightweight tags use the committed date. Trees, blobs and deeper tag chains
    give ``None``.
    """
    tag_date: str | None = None
    if target is not None and target.typename == "Tag":
        tag_date = target.tagger.date if target.tagger else None
    while target is not None and target.typename == "Tag":
        target = target.target
    if target is None or target.typename != "Commit" or not target.oid:
        return None
    return TagCommit(sha=target.oid, date=tag_date or target.committedDate)


def github_upstream_status(error: GithubException) -> int:
    """Return the HTTP status to report for a GitHub failure.

    GitHub answers a GraphQL rate limit with HTTP 200 and an error of type
    ``RATE_LIMITED``, which PyGithub raises as a 400; secondary limits arrive
    as 403s that PyGithub raises as ``RateLimitExceededException``. Both are
    reported as 429 so callers see an upstream limit, not a bad request.
    """
    if isinstance(error, RateLimitExceededException):
        return 429
    match error.data:
        case {"errors": list(errors)} if any(
            isinstance(item, dict) and item.get("type") == "RATE_LIMITED"
            for item in errors
        ):
            return 429
        case _:
            return error.status


def gitlab_commit_date(commit: GitLabCommit) -> str | None:
    """Return the date workspace sync reports for a GitLab commit."""
    return commit.authored_date or commit.committed_date or commit.created_at


def gitlab_tag_commit(tag: GitLabTag) -> TagCommit | None:
    """Return the commit a GitLab tag points at, dated by the tag if annotated."""
    if tag.commit is None:
        return None
    return TagCommit(
        sha=tag.commit.id, date=tag.created_at or gitlab_commit_date(tag.commit)
    )


def bitbucket_tag_commit(tag: BitbucketTag) -> TagCommit | None:
    """Return the commit a Bitbucket Cloud tag points at, if it is a commit."""
    if tag.target.type != "commit":
        return None
    return TagCommit(sha=tag.target.hash, date=tag.date or tag.target.date)
