"""Safe host-side file operations for sandbox-controlled bind mounts."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import orjson

from tracecat.sandbox.exceptions import SandboxFileSafetyError

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _relative_parts(relative_path: Path) -> tuple[str, ...]:
    """Validate and return components for a path beneath a trusted root."""
    if relative_path.is_absolute():
        raise SandboxFileSafetyError("Sandbox file path must be relative")

    parts = relative_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SandboxFileSafetyError("Sandbox file path contains unsafe components")
    return parts


def _open_directory(path: Path) -> int:
    """Open a trusted root without following a replacement symlink."""
    try:
        return os.open(path, _DIRECTORY_OPEN_FLAGS)
    except OSError as exc:
        raise SandboxFileSafetyError(
            "Sandbox file root is not a safe directory"
        ) from exc


@contextmanager
def _open_parent_directory(
    root: Path,
    relative_path: Path,
    *,
    create: bool,
) -> Iterator[tuple[int, str]]:
    """Open a file's parent beneath root using no-follow directory handles."""
    parts = _relative_parts(relative_path)
    if create:
        root.mkdir(parents=True, exist_ok=True)

    directory_fd = _open_directory(root)
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(
                        part,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise SandboxFileSafetyError(
                        "Sandbox file parent is not a safe directory"
                    ) from exc
            except OSError as exc:
                raise SandboxFileSafetyError(
                    "Sandbox file parent is not a safe directory"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
        yield directory_fd, parts[-1]
    except FileNotFoundError:
        raise
    finally:
        os.close(directory_fd)


def read_regular_file_beneath(
    root: Path,
    relative_path: Path,
    *,
    max_bytes: int,
    offset: int = 0,
) -> bytes | None:
    """Read a bounded regular file beneath root without following symlinks.

    Args:
        root: Trusted root directory containing sandbox-controlled files.
        relative_path: File path relative to root.
        max_bytes: Maximum bytes to read after offset.
        offset: Byte offset from which to begin reading.

    Returns:
        File bytes, or None when the file does not exist.

    Raises:
        SandboxFileSafetyError: If the path is unsafe, not a regular file, or
            exceeds the read limit.
    """
    if max_bytes < 0 or offset < 0:
        raise ValueError("max_bytes and offset must be non-negative")

    try:
        with _open_parent_directory(
            root,
            relative_path,
            create=False,
        ) as (parent_fd, filename):
            try:
                file_fd = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise SandboxFileSafetyError(
                    "Sandbox file is not safe to read"
                ) from exc
    except FileNotFoundError:
        return None

    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SandboxFileSafetyError("Sandbox file is not a regular file")
        if max(0, file_stat.st_size - offset) > max_bytes:
            raise SandboxFileSafetyError("Sandbox file exceeds the read limit")

        os.lseek(file_fd, offset, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise SandboxFileSafetyError("Sandbox file exceeds the read limit")
        return data
    except OSError as exc:
        raise SandboxFileSafetyError("Sandbox file could not be read safely") from exc
    finally:
        os.close(file_fd)


def read_json_object_beneath(
    root: Path,
    relative_path: Path,
    *,
    max_bytes: int,
) -> dict[str, Any] | None:
    """Read a bounded JSON object from a sandbox-controlled directory."""
    data = read_regular_file_beneath(
        root,
        relative_path,
        max_bytes=max_bytes,
    )
    if data is None:
        return None
    try:
        value = orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        raise SandboxFileSafetyError("Sandbox result is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SandboxFileSafetyError("Sandbox result is not a JSON object")
    return value


def regular_file_size_beneath(root: Path, relative_path: Path) -> int | None:
    """Return a regular file's size without following sandbox-created symlinks."""
    try:
        with _open_parent_directory(
            root,
            relative_path,
            create=False,
        ) as (parent_fd, filename):
            try:
                file_fd = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise SandboxFileSafetyError(
                    "Sandbox file is not safe to inspect"
                ) from exc
    except FileNotFoundError:
        return None

    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SandboxFileSafetyError("Sandbox file is not a regular file")
        return file_stat.st_size
    finally:
        os.close(file_fd)


def atomic_write_file_beneath(
    root: Path,
    relative_path: Path,
    data: bytes,
    *,
    mode: int = 0o600,
) -> None:
    """Atomically write beneath root without following sandbox-created symlinks."""
    with _open_parent_directory(
        root,
        relative_path,
        create=True,
    ) as (parent_fd, filename):
        temporary_name = f".{filename}.{uuid.uuid4().hex}.tmp"
        file_fd: int | None = None
        try:
            file_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
            view = memoryview(data)
            while view:
                written = os.write(file_fd, view)
                view = view[written:]
            os.fsync(file_fd)
            os.close(file_fd)
            file_fd = None
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError as exc:
            raise SandboxFileSafetyError(
                "Sandbox file could not be written safely"
            ) from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def ensure_directory_beneath(root: Path, relative_path: Path) -> None:
    """Create a directory beneath root without following existing symlinks."""
    sentinel = relative_path / ".directory-sentinel"
    with _open_parent_directory(root, sentinel, create=True):
        pass


def copy_tree_without_following_symlinks(source: Path, destination: Path) -> bool:
    """Copy a stopped sandbox's tree while preserving, not following, symlinks.

    The sandbox process must be stopped before this function is called so the
    validated tree cannot be changed between validation and copy.

    Returns:
        True if a non-empty tree was copied, otherwise False.
    """
    try:
        source_stat = source.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(source_stat.st_mode):
        raise SandboxFileSafetyError("Sandbox package cache is not a directory")

    has_entries = False
    for directory, directory_names, filenames in os.walk(
        source,
        topdown=True,
        followlinks=False,
    ):
        for name in [*directory_names, *filenames]:
            has_entries = True
            entry_mode = (Path(directory) / name).lstat().st_mode
            if not (
                stat.S_ISREG(entry_mode)
                or stat.S_ISDIR(entry_mode)
                or stat.S_ISLNK(entry_mode)
            ):
                raise SandboxFileSafetyError(
                    "Sandbox package cache contains a special file"
                )

    if not has_entries:
        return False
    shutil.copytree(source, destination, symlinks=True)
    return True
