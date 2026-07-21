"""Safe host-side file operations for sandbox-controlled bind mounts."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from tracecat.sandbox.exceptions import SandboxFileSafetyError


@dataclass(frozen=True, slots=True)
class _PlatformFlags:
    directory_open: int
    regular_file_read: int
    atomic_file_write: int


def _platform_flags() -> _PlatformFlags:
    """Return safe open flags, failing closed on unsupported platforms."""
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    dir_fd_functions = (os.open, os.mkdir, os.rename, os.unlink)
    if (
        directory is None
        or nofollow is None
        or nonblock is None
        or not all(function in supports_dir_fd for function in dir_fd_functions)
    ):
        raise SandboxFileSafetyError("Safe sandbox file I/O requires a POSIX platform")

    return _PlatformFlags(
        directory_open=os.O_RDONLY | directory | nofollow,
        regular_file_read=os.O_RDONLY | nofollow | nonblock,
        atomic_file_write=os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
    )


def _relative_parts(relative_path: Path) -> tuple[str, ...]:
    """Validate and return components for a path beneath a trusted root."""
    if relative_path.is_absolute():
        raise SandboxFileSafetyError("Sandbox file path must be relative")

    parts = relative_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SandboxFileSafetyError("Sandbox file path contains unsafe components")
    return parts


def _open_directory(path: Path, flags: _PlatformFlags) -> int:
    """Open a trusted root without following a replacement symlink."""
    try:
        return os.open(path, flags.directory_open)
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
    flags = _platform_flags()
    parts = _relative_parts(relative_path)
    if create:
        root.mkdir(parents=True, exist_ok=True)

    directory_fd = _open_directory(root, flags)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass

            try:
                next_fd = os.open(
                    part,
                    flags.directory_open,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise SandboxFileSafetyError(
                    "Sandbox file parent is not a safe directory"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
        yield directory_fd, parts[-1]
    finally:
        os.close(directory_fd)


@contextmanager
def _open_regular_file_beneath(
    root: Path,
    relative_path: Path,
    *,
    unsafe_message: str,
) -> Iterator[tuple[int, os.stat_result]]:
    """Open and validate a regular file beneath root without following links."""
    with _open_parent_directory(
        root,
        relative_path,
        create=False,
    ) as (parent_fd, filename):
        try:
            file_fd = os.open(
                filename,
                _platform_flags().regular_file_read,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SandboxFileSafetyError(unsafe_message) from exc

    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SandboxFileSafetyError("Sandbox file is not a regular file")
        yield file_fd, file_stat
    finally:
        os.close(file_fd)


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
        with _open_regular_file_beneath(
            root,
            relative_path,
            unsafe_message="Sandbox file is not safe to read",
        ) as (file_fd, file_stat):
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
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SandboxFileSafetyError("Sandbox file could not be read safely") from exc


def read_complete_lines_beneath(
    root: Path,
    relative_path: Path,
    *,
    offset: int,
    max_bytes: int,
) -> tuple[bytes, int] | None:
    """Read a bounded chunk ending at the last complete line beneath root."""
    if max_bytes < 0 or offset < 0:
        raise ValueError("max_bytes and offset must be non-negative")

    try:
        with _open_regular_file_beneath(
            root,
            relative_path,
            unsafe_message="Sandbox file is not safe to read",
        ) as (file_fd, _):
            os.lseek(file_fd, offset, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = max_bytes
            while remaining > 0:
                chunk = os.read(file_fd, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)

            data = b"".join(chunks)
            newline_index = data.rfind(b"\n")
            if newline_index < 0:
                if max_bytes > 0 and len(data) == max_bytes:
                    raise SandboxFileSafetyError(
                        "Sandbox file contains a line exceeding the read limit"
                    )
                return b"", offset

            complete_lines = data[: newline_index + 1]
            return complete_lines, offset + len(complete_lines)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SandboxFileSafetyError("Sandbox file could not be read safely") from exc


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
        with _open_regular_file_beneath(
            root,
            relative_path,
            unsafe_message="Sandbox file is not safe to inspect",
        ) as (_, file_stat):
            return file_stat.st_size
    except FileNotFoundError:
        return None


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
                _platform_flags().atomic_file_write,
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
    # The sentinel is never created; its parent walk forces the directory chain.
    sentinel = relative_path / ".directory-sentinel"
    with _open_parent_directory(root, sentinel, create=True):
        pass


def copy_tree_without_following_symlinks(
    source: Path,
    destination: Path,
    *,
    max_bytes: int,
    max_entries: int,
) -> bool:
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

    if max_bytes < 0 or max_entries < 0:
        raise ValueError("max_bytes and max_entries must be non-negative")

    entry_count = 0
    total_bytes = 0
    for directory, directory_names, filenames in os.walk(
        source,
        topdown=True,
        followlinks=False,
    ):
        for name in [*directory_names, *filenames]:
            entry_count += 1
            if entry_count > max_entries:
                raise SandboxFileSafetyError(
                    "Sandbox package cache exceeds the entry limit"
                )

            entry_stat = (Path(directory) / name).lstat()
            entry_mode = entry_stat.st_mode
            if not (
                stat.S_ISREG(entry_mode)
                or stat.S_ISDIR(entry_mode)
                or stat.S_ISLNK(entry_mode)
            ):
                raise SandboxFileSafetyError(
                    "Sandbox package cache contains a special file"
                )
            if stat.S_ISREG(entry_mode):
                total_bytes += entry_stat.st_size
                if total_bytes > max_bytes:
                    raise SandboxFileSafetyError(
                        "Sandbox package cache exceeds the byte limit"
                    )

    if entry_count == 0:
        return False
    shutil.copytree(source, destination, symlinks=True)
    return True
