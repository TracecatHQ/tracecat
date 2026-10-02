"""Domain types for the platform skill library."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LibrarySkill:
    """Platform-owned skill content bundled with the running image."""

    slug: str
    description: str | None
    files: Mapping[str, bytes]
    """Relative POSIX path to content; always includes ``SKILL.md``."""
    declared_tools: tuple[str, ...] = ()
    """Registry tool IDs from ``metadata.tools``."""

    @property
    def markdown(self) -> bytes:
        """Return the root ``SKILL.md`` content."""
        return self.files["SKILL.md"]
