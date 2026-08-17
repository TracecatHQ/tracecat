"""Fail-closed mount-state inspection for registry artifact caches."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def is_mount(path: Path) -> bool:
    """Return whether a path is mounted without hiding inspection failures."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False

    if stat.S_ISLNK(path_stat.st_mode):
        return False

    parent = Path(os.path.realpath(path / "..", strict=True))
    parent_stat = parent.lstat()
    return (
        path_stat.st_dev != parent_stat.st_dev or path_stat.st_ino == parent_stat.st_ino
    )
