"""API schemas for the platform skill library."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from tracecat import config
from tracecat.agent.skill.schemas import SkillName
from tracecat.core.schemas import Schema


class LibrarySkillBatchInstall(Schema):
    """Library skills to install together in one transaction."""

    slugs: list[SkillName] = Field(
        min_length=1, max_length=config.TRACECAT__LIMIT_CURSOR_MAX
    )


class LibrarySkillBatchUninstall(Schema):
    """Library skills to uninstall together or reject without removing any."""

    slugs: list[SkillName] = Field(
        min_length=1, max_length=config.TRACECAT__LIMIT_CURSOR_MAX
    )


class LibrarySkillSourceRead(Schema):
    """Library group, maintainer, and optional upstream location of a skill."""

    group: str | None
    """Platform the skill works with; null for a standalone skill."""
    group_summary: str | None = Field(default=None)
    """One-line group summary for the library list."""
    group_description: str | None = Field(default=None)
    """What the group is and why its skills exist."""
    summary: str | None = Field(default=None)
    """One-line summary of a standalone skill for the library list."""
    provider: str | None = Field(default=None)
    """Maintainer when it is not the group itself; null means official."""
    repo: str | None
    commit: str | None
    license: str
    url: str | None
    kind: Literal["upstream", "local"]


class LibrarySkillRead(Schema):
    """A library skill and this workspace's install state."""

    slug: str
    description: str | None = Field(default=None)
    installed: bool
    source: LibrarySkillSourceRead | None = Field(default=None)


class LibrarySkillFileRead(Schema):
    """One file bundled with a library skill."""

    path: str
    size_bytes: int
    content: str | None = Field(default=None)
    """UTF-8 text, or ``None`` for binary files."""


class LibrarySkillDetailRead(LibrarySkillRead):
    """A library skill with its bundled files, for the read-only preview."""

    files: list[LibrarySkillFileRead]
