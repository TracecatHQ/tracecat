"""The package include file: which source workspace resources form the package.

The include file lives at the root of the source repository. It is an
allowlist in gitignore syntax: a resource is in the package when one of its
export paths matches, and everything else is left out. Patterns match the
paths that workspace sync writes, for example ``workflows/<id>/**`` or
``tables/<id>/table.yml``. ``!pattern`` excludes a path again, as in
``.gitignore``.

This module only parses the file and matches it against export paths. The
sync service turns the matches into a projection, because that needs the
database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pathspec import GitIgnoreSpec

from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.enums import SyncResourceType

PACKAGE_INCLUDE_FILENAME = ".tracecatinclude"
"""Repository-root path of the package include file."""

_INCLUDE_FILE_HEADER = (
    "# Managed resources package. Written by Tracecat; one export path\n"
    "# pattern per line, in gitignore syntax. Only matching resources ship.\n"
)


class PackageIncludeError(TracecatValidationError):
    """The include file has a pattern that cannot be parsed."""


@dataclass(frozen=True, slots=True)
class PackageInclude:
    """A parsed include file."""

    patterns: tuple[str, ...]
    """Pattern lines in file order, without comments or blank lines."""
    spec: GitIgnoreSpec
    """Compiled matcher for the patterns."""

    def matches(self, path: str) -> bool:
        """Return whether the export path ``path`` is in the package."""
        return self.spec.match_file(path)


@dataclass(frozen=True, slots=True)
class PackageResourceKey:
    """One resource in a package, by type and Git source id."""

    resource_type: SyncResourceType
    source_id: str


def parse_package_include(content: str) -> PackageInclude:
    """Parse include file text.

    An empty file is valid and selects nothing.

    Raises:
        PackageIncludeError: A line is not a valid gitignore pattern.
    """
    patterns = tuple(
        stripped
        for line in content.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )
    try:
        spec = GitIgnoreSpec.from_lines(patterns)
    except ValueError as e:
        raise PackageIncludeError(f"Invalid include file pattern: {e}") from e
    return PackageInclude(patterns=patterns, spec=spec)


def render_package_include(patterns: Iterable[str]) -> str:
    """Render include file text from patterns, one per line, in order.

    Order is kept because a later gitignore line overrides an earlier one:
    ``workflows/**`` then ``!workflows/x/**`` excludes ``x``, but the reverse
    order includes it. Only exact repeats are dropped.

    Raises:
        PackageIncludeError: A pattern is not valid gitignore syntax.
    """
    lines = list(
        dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip())
    )
    content = _INCLUDE_FILE_HEADER + "".join(f"{line}\n" for line in lines)
    # Parse once so a bad pattern never reaches the repository.
    parse_package_include(content)
    return content


def matched_resources(
    include: PackageInclude,
    paths_by_resource: Mapping[PackageResourceKey, Iterable[str]],
) -> set[PackageResourceKey]:
    """Return the resources with at least one export path the include matches."""
    return {
        key
        for key, paths in paths_by_resource.items()
        if any(include.matches(path) for path in paths)
    }


def unmatched_patterns(include: PackageInclude, paths: Iterable[str]) -> list[str]:
    """Return positive patterns that match none of ``paths``.

    Negations are skipped: a ``!pattern`` that removes nothing is harmless.
    """
    path_list = list(paths)
    unmatched: list[str] = []
    for pattern in include.patterns:
        if pattern.startswith("!"):
            continue
        single = GitIgnoreSpec.from_lines([pattern])
        if not any(single.match_file(path) for path in path_list):
            unmatched.append(pattern)
    return unmatched
