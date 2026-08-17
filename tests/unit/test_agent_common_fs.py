from __future__ import annotations

import shutil
import stat
from pathlib import Path

from tracecat.agent.common.fs import force_rmtree


def test_force_rmtree_does_not_follow_symlinks(tmp_path: Path) -> None:
    """Cleanup never changes or removes file and directory targets outside its root."""
    outside_file = tmp_path / "outside-file"
    outside_file.write_text("sentinel")
    outside_file.chmod(0o640)
    outside_file_mode = stat.S_IMODE(outside_file.stat().st_mode)

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    outside_content = outside_dir / "sentinel"
    outside_content.write_text("untouched")
    outside_dir.chmod(0o750)
    outside_dir_mode = stat.S_IMODE(outside_dir.stat().st_mode)

    root = tmp_path / "job"
    containing_dir = root / "uv-state" / "cache"
    containing_dir.mkdir(parents=True)
    (containing_dir / "file-link").symlink_to(outside_file)
    (containing_dir / "dir-link").symlink_to(
        outside_dir,
        target_is_directory=True,
    )
    containing_dir.chmod(0o555)

    try:
        force_rmtree(root)

        assert not root.exists()
        assert outside_file.read_text() == "sentinel"
        assert stat.S_IMODE(outside_file.stat().st_mode) == outside_file_mode
        assert outside_content.read_text() == "untouched"
        assert stat.S_IMODE(outside_dir.stat().st_mode) == outside_dir_mode
    finally:
        if containing_dir.exists():
            containing_dir.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_force_rmtree_removes_mode_zero_directory(tmp_path: Path) -> None:
    """Cleanup opens a mode-000 directory before descending into nested content."""
    root = tmp_path / "job"
    blocked_dir = root / "uv-state" / "blocked"
    nested_dir = blocked_dir / "nested"
    nested_dir.mkdir(parents=True)
    (nested_dir / "artifact").write_text("cached")
    blocked_dir.chmod(0o000)

    try:
        force_rmtree(root)

        assert not root.exists()
    finally:
        if blocked_dir.exists():
            blocked_dir.chmod(0o700)
        if nested_dir.exists():
            nested_dir.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_force_rmtree_removes_read_only_root(tmp_path: Path) -> None:
    """Cleanup restores owner access when the tree root itself is mode 0555."""
    root = tmp_path / "uv-state"
    root.mkdir()
    (root / "artifact").write_text("cached")
    root.chmod(0o555)

    try:
        force_rmtree(root)

        assert not root.exists()
    finally:
        if root.exists():
            root.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_force_rmtree_missing_root_is_noop(tmp_path: Path) -> None:
    """Cleanup is idempotent when the requested root is already absent."""
    root = tmp_path / "missing"

    force_rmtree(root)

    assert not root.exists()


def test_force_rmtree_removes_deep_read_only_directories(tmp_path: Path) -> None:
    """Cleanup normalizes read-only directories at every level of a deep tree."""
    root = tmp_path / "job"
    first_level = root / "uv-state" / "first"
    second_level = first_level / "second"
    third_level = second_level / "third"
    third_level.mkdir(parents=True)
    (third_level / "artifact").write_text("cached")
    second_level.chmod(0o555)
    first_level.chmod(0o555)

    try:
        force_rmtree(root)

        assert not root.exists()
    finally:
        if first_level.exists():
            first_level.chmod(0o700)
        if second_level.exists():
            second_level.chmod(0o700)
        if third_level.exists():
            third_level.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)
