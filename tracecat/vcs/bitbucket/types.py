"""Validated subset of Bitbucket Cloud REST responses."""

from pydantic import BaseModel, Field


class BitbucketBranch(BaseModel):
    name: str


class BitbucketRepository(BaseModel):
    mainbranch: BitbucketBranch | None = None


class BitbucketAuthor(BaseModel):
    raw: str = ""


class BitbucketCommit(BaseModel):
    hash: str
    message: str
    date: str
    author: BitbucketAuthor


class BitbucketTagTarget(BaseModel):
    type: str
    """Only ``commit`` targets can be rolled out; anything else is skipped."""
    hash: str
    date: str | None = None


class BitbucketTag(BaseModel):
    name: str
    target: BitbucketTagTarget
    date: str | None = None
    """Tagger date; Bitbucket sets it only for annotated tags."""


class BitbucketLink(BaseModel):
    href: str


class BitbucketLinks(BaseModel):
    html: BitbucketLink


class BitbucketPullRequest(BaseModel):
    id: int
    links: BitbucketLinks


class BitbucketPage[T](BaseModel):
    values: list[T] = Field(default_factory=list)
    next: str | None = None
