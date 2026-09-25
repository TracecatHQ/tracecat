"""Validated subset of Bitbucket Cloud REST responses."""

from dataclasses import dataclass

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


class BitbucketLink(BaseModel):
    href: str


class BitbucketLinks(BaseModel):
    html: BitbucketLink


class BitbucketPullRequest(BaseModel):
    id: int
    links: BitbucketLinks


class BitbucketPage[T: BaseModel](BaseModel):
    values: list[T] = Field(default_factory=list)
    next: str | None = None


@dataclass(frozen=True, slots=True)
class GitEntry:
    mode: str
    sha: str
