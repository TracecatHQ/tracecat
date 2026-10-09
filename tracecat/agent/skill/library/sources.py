"""Local skills and pinned upstream sources for the bundled skill library.

``sources.toml`` groups skills by the platform they work with. ``provider``
names the maintainer only when it is not that platform; omitted means official.
An entry without a group lists standalone skills, one library entry each.
Upstream entries pin repositories and file digests; local entries are skills
maintained in this repository. ``scripts/skill_library.py`` rewrites only
upstream content, so a hand edit or a forgotten sync fails the bundled-library
test offline.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from tracecat.agent.skill.library.types import (
    LibrarySkillSource,
    LibrarySource,
    LibrarySourceEntry,
    LocalLibrarySource,
)
from tracecat.agent.skill.schemas import SkillName

SOURCES_PATH = Path(__file__).parent / "sources.toml"

_RepoPath = Annotated[str, StringConstraints(pattern=r"^[^/\\][^\\]*$")]
_Summary = Annotated[str, StringConstraints(min_length=1, max_length=60)]
"""One line in the library list; the long text stays on the detail page."""


class _SourceFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: Annotated[str, StringConstraints(min_length=1)] | None = Field(default=None)
    provider: Annotated[str, StringConstraints(min_length=1)] | None = Field(
        default=None
    )
    license: Annotated[str, StringConstraints(min_length=1)]
    skills: dict[SkillName, _RepoPath] = Field(min_length=1)
    summaries: dict[SkillName, _Summary] = Field(default_factory=dict)
    """List-row summary per skill; standalone sources only."""

    @field_validator("skills")
    @classmethod
    def _stay_inside_repo(cls, value: dict[str, str]) -> dict[str, str]:
        if any(".." in path.split("/") for path in value.values()):
            raise ValueError("skill paths must stay inside the repository")
        return {slug: path.rstrip("/") for slug, path in value.items()}

    @model_validator(mode="after")
    def _name_a_maintainer(self) -> _SourceFields:
        if self.group is None and self.provider is None:
            raise ValueError("set a group, or a provider for standalone skills")
        if self.provider == self.group:
            raise ValueError("omit provider when the group maintains the skills")
        if self.group is not None and self.summaries:
            raise ValueError("grouped skills use their group's summary")
        if self.group is None and self.summaries.keys() != self.skills.keys():
            raise ValueError("give each standalone skill exactly one summary")
        return self


class _SourceEntry(_SourceFields):
    kind: Literal["upstream"]
    repo: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ]
    commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    tree_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    exclude: tuple[str, ...] = Field(default=())


class _LocalSourceEntry(_SourceFields):
    kind: Literal["local"]

    @field_validator("skills")
    @classmethod
    def _match_bundled_directory(cls, value: dict[str, str]) -> dict[str, str]:
        if any(path != f"skills/{slug}" for slug, path in value.items()):
            raise ValueError("local skill paths must be skills/<slug>")
        return value


class _GroupEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: _Summary
    description: Annotated[str, StringConstraints(min_length=1)]


class _SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    groups: dict[str, _GroupEntry] = Field(default_factory=dict)
    """Summary and description of each group."""
    source: tuple[
        Annotated[_SourceEntry | _LocalSourceEntry, Field(discriminator="kind")], ...
    ] = Field(default=())


def parse_sources(text: str) -> list[LibrarySourceEntry]:
    """Validate manifest TOML into its sources, in file order."""

    manifest = _SourceManifest.model_validate(tomllib.loads(text))
    repos: set[str] = set()
    groups: dict[str, str] = {}
    slugs: set[str] = set()
    sources: list[LibrarySourceEntry] = []
    for entry in manifest.source:
        if isinstance(entry, _SourceEntry):
            if entry.repo in repos:
                raise ValueError(f"Library source {entry.repo!r} is listed twice")
            repos.add(entry.repo)
        if entry.group is not None:
            # The UI keys library pages by this slug; one spelling per group.
            group = re.sub(r"[^a-z0-9]+", "-", entry.group.lower()).strip("-")
            if not group or groups.setdefault(group, entry.group) != entry.group:
                raise ValueError(
                    f"Library group {entry.group!r} has a conflicting URL slug"
                )
        if repeated := sorted(slugs & entry.skills.keys()):
            raise ValueError(f"Library skills listed twice: {', '.join(repeated)}")
        slugs.update(entry.skills)
        described = manifest.groups.get(entry.group) if entry.group else None
        if entry.group is not None and described is None:
            raise ValueError(f"Library group {entry.group!r} has no description")
        group_summary = described.summary if described else None
        group_description = described.description if described else None
        if isinstance(entry, _LocalSourceEntry):
            sources.append(
                LocalLibrarySource(
                    **entry.model_dump(),
                    group_summary=group_summary,
                    group_description=group_description,
                )
            )
        else:
            sources.append(
                LibrarySource(
                    **entry.model_dump(),
                    group_summary=group_summary,
                    group_description=group_description,
                )
            )
    if unused := sorted(manifest.groups.keys() - groups.values()):
        raise ValueError(f"Library groups without sources: {', '.join(unused)}")
    return sources


def load_sources(path: Path = SOURCES_PATH) -> list[LibrarySourceEntry]:
    """Load the pinned source manifest."""

    return parse_sources(path.read_text(encoding="utf-8"))


def skill_sources(
    sources: Sequence[LibrarySourceEntry],
) -> dict[str, LibrarySkillSource]:
    """Key each skill's provenance by slug."""

    return {
        slug: source.skill_source(slug) for source in sources for slug in source.skills
    }


def read_tree(root: Path) -> dict[str, bytes]:
    """Read every regular file under ``root``, keyed by relative POSIX path."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        # Running a bundled helper script locally must not look like drift.
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }


def source_tree(root: Path, source: LibrarySource) -> dict[str, bytes]:
    """Read a source's synced files, keyed by ``<slug>/<path>``."""

    return {
        f"{slug}/{path}": data
        for slug in sorted(source.skills)
        for path, data in read_tree(root / slug).items()
    }


def tree_digest(files: Mapping[str, bytes]) -> str:
    """Hash file paths and contents in a stable order."""

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[path]).digest())
    return digest.hexdigest()


def verify_library(root: Path, sources: Sequence[LibrarySourceEntry]) -> list[str]:
    """List every way the bundled directories disagree with the manifest."""

    present = (
        {path.name for path in root.iterdir() if path.is_dir()}
        if root.is_dir()
        else set()
    )
    listed = {slug for source in sources for slug in source.skills}
    problems = [
        f"{slug}: directory has no sources.toml entry"
        for slug in sorted(present - listed)
    ]
    for source in sources:
        owner = source.group or source.provider
        if isinstance(source, LocalLibrarySource):
            if missing := sorted(source.skills.keys() - present):
                problems.append(f"{owner}: {', '.join(missing)} missing locally")
            continue
        sync = f"run `just skill-library sync {source.repo}`"
        if missing := sorted(source.skills.keys() - present):
            problems.append(f"{owner}: {', '.join(missing)} not synced; {sync}")
        elif tree_digest(source_tree(root, source)) != source.tree_sha256:
            problems.append(
                f"{owner}: files differ from {source.repo}@{source.commit[:12]}; {sync}"
            )
    return problems
