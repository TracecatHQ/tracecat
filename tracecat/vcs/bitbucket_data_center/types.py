"""Data Center REST response contracts, independent of Cloud's API."""

from pydantic import BaseModel, Field


class DataCenterBranch(BaseModel):
    id: str
    name: str = Field(alias="displayId")
    isDefault: bool = False


class DataCenterAuthor(BaseModel):
    name: str
    emailAddress: str = ""


class DataCenterCommit(BaseModel):
    id: str
    message: str
    authorTimestamp: int
    author: DataCenterAuthor


class DataCenterProject(BaseModel):
    key: str


class DataCenterRepository(BaseModel):
    slug: str
    project: DataCenterProject


class DataCenterRef(BaseModel):
    id: str
    repository: DataCenterRepository


class DataCenterPullRequest(BaseModel):
    id: int
    fromRef: DataCenterRef
    toRef: DataCenterRef


class DataCenterPage[T: BaseModel](BaseModel):
    values: list[T] = Field(default_factory=list)
    isLastPage: bool
    nextPageStart: int | None = None
