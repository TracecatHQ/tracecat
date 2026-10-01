"""Load the platform skill library bundled with this package.

Each directory under ``skills/`` is one library skill. Content is repo-owned
and immutable per image, so a malformed entry fails loudly instead of being
skipped; a bundled-catalog test keeps the shipped entries valid.
"""

from __future__ import annotations

import importlib.resources as resources
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from tracecat.agent.skill.builtin import PLATFORM_SKILLS
from tracecat.agent.skill.frontmatter import parse_skill_markdown
from tracecat.agent.skill.library.types import LibrarySkill
from tracecat.exceptions import TracecatValidationError

_LIBRARY_PACKAGE = "tracecat.agent.skill.library"
_LIBRARY_DIR = "skills"
# Library skills stage into the same plugin as platform skills.
_RESERVED_NAMES = frozenset(
    name for skill in PLATFORM_SKILLS for name in (skill.skill_name, skill.asset_name)
)


def _load_skill(skill_dir: Path) -> LibrarySkill:
    slug = skill_dir.name
    if slug in _RESERVED_NAMES:
        raise ValueError(f"Library skill {slug!r} shadows a platform skill")
    paths = sorted(skill_dir.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError(f"Library skill {slug!r} contains a symlink")
    files = {
        path.relative_to(skill_dir).as_posix(): path.read_bytes()
        for path in paths
        if path.is_file()
    }
    if "SKILL.md" not in files:
        raise ValueError(f"Library skill {slug!r} is missing SKILL.md")
    # Subagents preload only SKILL.md and have no Read grant for extra files.
    if set(files) != {"SKILL.md"}:
        raise ValueError(f"Library skill {slug!r} must contain only SKILL.md")
    frontmatter = parse_skill_markdown(files["SKILL.md"].decode("utf-8"))
    if frontmatter is None or frontmatter.name != slug:
        raise ValueError(f"Library skill {slug!r} frontmatter name must match")
    # Crawl scope: library skills carry instructions only, never tool grants.
    if frontmatter.metadata.tools:
        raise ValueError(f"Library skill {slug!r} must not declare tools")
    return LibrarySkill(
        slug=slug,
        description=frontmatter.description,
        markdown=files["SKILL.md"],
    )


def load_library_from(root: Path) -> dict[str, LibrarySkill]:
    """Load and validate every library skill under ``root``, keyed by slug."""

    if not root.is_dir():
        return {}
    return {
        skill.slug: skill
        for skill in (
            _load_skill(path) for path in sorted(root.iterdir()) if path.is_dir()
        )
    }


@lru_cache(maxsize=1)
def load_library() -> dict[str, LibrarySkill]:
    """Return the bundled library, loaded once per process."""

    root = Path(str(resources.files(_LIBRARY_PACKAGE).joinpath(_LIBRARY_DIR)))
    return load_library_from(root)


def get_library_skills(slugs: Sequence[str]) -> list[LibrarySkill]:
    """Resolve slugs against the bundled library, in the given order.

    Raises:
        TracecatValidationError: If any slug is not in the library.
    """

    library = load_library()
    if missing := sorted(set(slugs) - library.keys()):
        raise TracecatValidationError(
            f"Unknown library skills: {', '.join(missing)}",
            detail={"code": "library_skill_not_found", "slugs": missing},
        )
    return [library[slug] for slug in slugs]
