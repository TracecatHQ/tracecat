"""Fail-closed mount-state inspection for registry artifact caches."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def is_mount(path: Path) -> bool:
    """Return whether a path is mounted without hiding inspection failures.

    ``Path.is_mount()`` delegates to ``os.path.ismount()``, which converts every
    ``OSError`` from ``lstat`` into ``False``. Cache cleanup must distinguish a
    missing mount directory from an unreadable one so it never deletes the
    backing image while mount state is unknown.
    """
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
