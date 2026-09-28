"""Filesystem helpers for agent runtime cleanup."""

import os
import shutil
from pathlib import Path


def force_rmtree(root: Path) -> None:
    """Delete a tree that sandboxed code may have made unreadable."""
    try:
        os.chmod(root, 0o700)
    except FileNotFoundError:
        return
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)
    shutil.rmtree(root)
