"""Repository-bound paths used by the internal load-test package."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

REPOSITORY_ROOT_ENV: Final = "TRACECAT_LOADTEST_REPO_ROOT"


class RepositoryRootError(RuntimeError):
    """The Tracecat repository root could not be resolved."""


def _is_repository_root(path: Path) -> bool:
    return (
        (path / "pyproject.toml").is_file()
        and (path / "scripts/cluster").is_file()
        and (path / "docker-compose.dev.yml").is_file()
    )


def resolve_repository_root(start: Path | None = None) -> Path:
    """Resolve the checkout that provides Compose and cluster orchestration."""
    configured_root = os.environ.get(REPOSITORY_ROOT_ENV)
    if configured_root:
        root = Path(configured_root).expanduser().resolve()
        if _is_repository_root(root):
            return root
        raise RepositoryRootError(
            f"{REPOSITORY_ROOT_ENV} does not identify a Tracecat checkout: {root}"
        )

    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if _is_repository_root(candidate):
            return candidate
    raise RepositoryRootError(
        "could not find a Tracecat checkout from the current directory; "
        f"set {REPOSITORY_ROOT_ENV}"
    )
