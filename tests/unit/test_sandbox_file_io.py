"""Regression tests for host I/O over sandbox-controlled bind mounts."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

import tracecat.sandbox.file_io as file_io_module
from tracecat.sandbox.exceptions import SandboxFileSafetyError
from tracecat.sandbox.file_io import (
    atomic_write_file_beneath,
    copy_tree_without_following_symlinks,
    read_complete_lines_beneath,
    read_json_object_beneath,
    read_regular_file_beneath,
)


def test_read_regular_file_beneath_reads_bounded_file(tmp_path: Path) -> None:
    """A normal result file beneath the trusted root should be readable."""
    (tmp_path / "result.json").write_text('{"success":true}')

    result = read_json_object_beneath(
        tmp_path,
        Path("result.json"),
        max_bytes=1024,
    )

    assert result == {"success": True}


def test_read_regular_file_beneath_rejects_final_symlink(tmp_path: Path) -> None:
    """The host must not dereference a result symlink selected by the sandbox."""
    outside_file = tmp_path.parent / "outside-result.json"
    outside_file.write_text('{"secret":"host data"}')
    (tmp_path / "result.json").symlink_to(outside_file)

    with pytest.raises(SandboxFileSafetyError, match="not safe to read"):
        read_regular_file_beneath(
            tmp_path,
            Path("result.json"),
            max_bytes=1024,
        )


def test_read_regular_file_beneath_rejects_parent_symlink(tmp_path: Path) -> None:
    """No-follow checks should cover every directory component, not only the file."""
    outside_dir = tmp_path.parent / "outside-session"
    outside_dir.mkdir()
    (outside_dir / "session.jsonl").write_text("host data")
    (tmp_path / ".claude").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(SandboxFileSafetyError, match="parent"):
        read_regular_file_beneath(
            tmp_path,
            Path(".claude/session.jsonl"),
            max_bytes=1024,
        )


def test_read_regular_file_beneath_rejects_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    """A sandbox-created FIFO must not block a trusted executor thread."""
    fifo_path = tmp_path / "result.json"
    os.mkfifo(fifo_path)

    with pytest.raises(SandboxFileSafetyError, match="regular file"):
        read_regular_file_beneath(
            tmp_path,
            Path("result.json"),
            max_bytes=1024,
        )


def test_read_regular_file_beneath_enforces_size_limit(tmp_path: Path) -> None:
    """A sandbox-controlled file cannot trigger an unbounded host read."""
    (tmp_path / "result.json").write_bytes(b"x" * 11)

    with pytest.raises(SandboxFileSafetyError, match="read limit"):
        read_regular_file_beneath(
            tmp_path,
            Path("result.json"),
            max_bytes=10,
        )


def test_read_complete_lines_beneath_drains_multiple_chunks(tmp_path: Path) -> None:
    """Successive bounded reads should drain every complete line in order."""
    session_data = b"one\n22\nthree\nfour\n"
    (tmp_path / "session.jsonl").write_bytes(session_data)

    chunks: list[bytes] = []
    offsets = [0]
    while True:
        result = read_complete_lines_beneath(
            tmp_path,
            Path("session.jsonl"),
            offset=offsets[-1],
            max_bytes=6,
        )
        assert result is not None
        chunk, next_offset = result
        if not chunk:
            break
        assert chunk.endswith(b"\n")
        chunks.append(chunk)
        offsets.append(next_offset)

    assert b"".join(chunks) == session_data
    assert offsets == [0, 4, 7, 13, len(session_data)]


def test_read_complete_lines_beneath_excludes_partial_trailing_line(
    tmp_path: Path,
) -> None:
    """An incomplete trailing line should remain unread for the next flush."""
    (tmp_path / "session.jsonl").write_bytes(b"complete\npartial")

    result = read_complete_lines_beneath(
        tmp_path,
        Path("session.jsonl"),
        offset=0,
        max_bytes=64,
    )

    assert result == (b"complete\n", len(b"complete\n"))


def test_read_complete_lines_beneath_rejects_oversized_line(tmp_path: Path) -> None:
    """A line wider than one socket frame can never be emitted safely."""
    (tmp_path / "session.jsonl").write_bytes(b"123456\n")

    with pytest.raises(SandboxFileSafetyError, match="line exceeding"):
        read_complete_lines_beneath(
            tmp_path,
            Path("session.jsonl"),
            offset=0,
            max_bytes=6,
        )


def test_atomic_write_replaces_symlink_without_touching_target(tmp_path: Path) -> None:
    """Session hydration should replace a planted link, not write through it."""
    outside_file = tmp_path.parent / "outside-session.jsonl"
    outside_file.write_text("host data")
    session_dir = tmp_path / ".claude" / "projects"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"
    session_file.symlink_to(outside_file)

    atomic_write_file_beneath(
        tmp_path,
        Path(".claude/projects/session.jsonl"),
        b"sandbox session",
    )

    assert outside_file.read_text() == "host data"
    assert not session_file.is_symlink()
    assert session_file.read_bytes() == b"sandbox session"


def test_atomic_write_rejects_parent_symlink(tmp_path: Path) -> None:
    """Session hydration must not create files through a planted parent link."""
    outside_dir = tmp_path.parent / "outside-parent"
    outside_dir.mkdir()
    (tmp_path / ".claude").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(SandboxFileSafetyError, match="parent"):
        atomic_write_file_beneath(
            tmp_path,
            Path(".claude/projects/session.jsonl"),
            b"sandbox session",
        )

    assert list(outside_dir.iterdir()) == []


def test_copy_tree_preserves_symlinks_instead_of_dereferencing(
    tmp_path: Path,
) -> None:
    """Package promotion must not copy bytes from a symlink's host target."""
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    outside_file = tmp_path / "host-secret"
    outside_file.write_text("host data")
    (source / "package-link").symlink_to(outside_file)

    copied = copy_tree_without_following_symlinks(
        source,
        destination,
        max_bytes=1024,
        max_entries=10,
    )

    assert copied is True
    copied_link = destination / "package-link"
    assert copied_link.is_symlink()
    assert os.readlink(copied_link) == str(outside_file)


def test_copy_tree_rejects_special_files(tmp_path: Path) -> None:
    """Package promotion should reject devices, sockets, and FIFOs."""
    source = tmp_path / "source"
    source.mkdir()
    os.mkfifo(source / "package-fifo")

    with pytest.raises(SandboxFileSafetyError, match="special file"):
        copy_tree_without_following_symlinks(
            source,
            tmp_path / "destination",
            max_bytes=1024,
            max_entries=10,
        )


def test_copy_tree_rejects_aggregate_bytes_over_limit(tmp_path: Path) -> None:
    """Package promotion should reject trees exceeding the byte budget."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.py").write_bytes(b"1234")

    with pytest.raises(SandboxFileSafetyError, match="byte limit"):
        copy_tree_without_following_symlinks(
            source,
            tmp_path / "destination",
            max_bytes=3,
            max_entries=10,
        )


def test_copy_tree_rejects_entries_over_limit(tmp_path: Path) -> None:
    """Directories, symlinks, and files should all consume the entry budget."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "package").mkdir()
    (source / "package" / "module.py").write_text("pass")

    with pytest.raises(SandboxFileSafetyError, match="entry limit"):
        copy_tree_without_following_symlinks(
            source,
            tmp_path / "destination",
            max_bytes=1024,
            max_entries=1,
        )


def test_file_io_imports_without_posix_flags_and_fails_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-POSIX platforms should import successfully and fail closed on use."""
    with monkeypatch.context() as platform:
        platform.delattr(file_io_module.os, "O_NOFOLLOW")
        importlib.reload(file_io_module)

        with pytest.raises(SandboxFileSafetyError, match="requires a POSIX platform"):
            file_io_module.read_regular_file_beneath(
                tmp_path,
                Path("result.json"),
                max_bytes=1024,
            )

    importlib.reload(file_io_module)
