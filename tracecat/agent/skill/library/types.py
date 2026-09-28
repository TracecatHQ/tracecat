"""Domain types for the platform skill library."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LibrarySkill:
    """Platform-owned skill content bundled with the running image."""

    slug: str
    description: str | None
    files: dict[str, bytes]
    """File contents keyed by path relative to the skill root."""
