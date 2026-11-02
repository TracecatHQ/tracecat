"""Domain types for the platform skill library."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class LibrarySkillSource:
    """Library group, maintainer, and optional upstream provenance for a skill."""

    group: str | None
    """Platform the skill works with; None for a standalone skill."""
    provider: str | None
    """Maintainer when it is not the group itself; None means official."""
    repo: str | None
    """GitHub ``owner/name``."""
    commit: str | None
    """Full commit SHA the bundled files were taken from."""
    path: str
    """Skill directory inside the repository."""
    license: str
    """SPDX identifier of this skill's license."""
    group_summary: str | None = None
    """One-line group summary for the library list."""
    group_description: str | None = None
    """What the group is and why its skills exist; None for standalone skills."""
    summary: str | None = None
    """One-line summary of a standalone skill for the library list."""
    kind: Literal["upstream", "local"] = "upstream"

    @property
    def url(self) -> str | None:
        """Return the upstream directory at the pinned commit."""
        if self.repo is None or self.commit is None:
            return None
        return f"https://github.com/{self.repo}/tree/{self.commit}/{self.path}"


@dataclass(frozen=True, slots=True)
class LibrarySource:
    """One pinned upstream repository and the skills synced from it."""

    group: str | None
    """None lists each skill as its own library entry."""
    provider: str | None
    repo: str
    commit: str
    license: str
    skills: Mapping[str, str]
    """Skill slug to its directory inside the repository."""
    tree_sha256: str
    """Digest of every synced file from this source; see ``sources.tree_digest``."""
    exclude: tuple[str, ...] = ()
    """Glob patterns, relative to each skill directory, left out of the sync."""
    group_summary: str | None = None
    group_description: str | None = None
    summaries: Mapping[str, str] = field(default_factory=dict)
    """List-row summary per skill; set only for standalone sources."""
    kind: Literal["upstream"] = "upstream"

    def skill_source(self, slug: str) -> LibrarySkillSource:
        """Return the provenance of one skill from this source."""
        return LibrarySkillSource(
            group=self.group,
            provider=self.provider,
            repo=self.repo,
            commit=self.commit,
            path=self.skills[slug],
            license=self.license,
            group_summary=self.group_summary,
            group_description=self.group_description,
            summary=self.summaries.get(slug),
        )


@dataclass(frozen=True, slots=True)
class LocalLibrarySource:
    """Skills maintained in this repository rather than synced upstream."""

    group: str | None
    """None lists each skill as its own library entry."""
    provider: str | None
    license: str
    skills: Mapping[str, str]
    """Paths relative to sources.toml, each exactly skills/<slug>."""
    group_summary: str | None = None
    group_description: str | None = None
    summaries: Mapping[str, str] = field(default_factory=dict)
    """List-row summary per skill; set only for standalone sources."""
    kind: Literal["local"] = "local"

    def skill_source(self, slug: str) -> LibrarySkillSource:
        """Return the local maintenance metadata for a listed skill."""
        return LibrarySkillSource(
            group=self.group,
            provider=self.provider,
            repo=None,
            commit=None,
            path=self.skills[slug],
            license=self.license,
            group_summary=self.group_summary,
            group_description=self.group_description,
            summary=self.summaries.get(slug),
            kind=self.kind,
        )


type LibrarySourceEntry = LibrarySource | LocalLibrarySource


@dataclass(frozen=True, slots=True)
class LibrarySkill:
    """Platform-owned skill content bundled with the running image."""

    slug: str
    description: str | None
    files: Mapping[str, bytes]
    """Relative POSIX path to content; always includes ``SKILL.md``."""
    declared_tools: tuple[str, ...] = ()
    """Registry tool IDs from ``metadata.tools``."""
    source: LibrarySkillSource | None = None
    """Maintenance and provenance metadata from the source manifest."""

    @property
    def markdown(self) -> bytes:
        """Return the root ``SKILL.md`` content."""
        return self.files["SKILL.md"]
