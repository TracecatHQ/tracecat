"""API schemas for the platform skill library."""

from __future__ import annotations

from pydantic import Field

from tracecat.core.schemas import Schema


class LibrarySkillRead(Schema):
    """A library skill and this workspace's install state."""

    slug: str
    description: str | None = Field(default=None)
    installed: bool
