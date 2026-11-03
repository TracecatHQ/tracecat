"""Schemas for platform build details."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class BuildEdition(StrEnum):
    """Edition of the running deployment."""

    OSS = "oss"
    EE = "ee"


class PlatformBuildInfo(BaseModel):
    """Version and build details of the running deployment."""

    version: str
    edition: BuildEdition
    commit: str | None = Field(default=None)
    built_at: datetime | None = Field(default=None)
