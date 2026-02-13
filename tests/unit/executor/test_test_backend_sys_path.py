from __future__ import annotations

import sys
import uuid

from tracecat.executor.backends.test import _temporary_sys_path


def _unique_sys_path_entry(prefix: str) -> str:
    return f"/tmp/{prefix}-{uuid.uuid4().hex}"


def test_temporary_sys_path_removes_only_inserted_entries() -> None:
    """Do not remove entries that already existed before entering the context."""
    existing_path = _unique_sys_path_entry("existing")
    inserted_path = _unique_sys_path_entry("inserted")
    original_sys_path = list(sys.path)

    try:
        sys.path.insert(0, existing_path)

        with _temporary_sys_path([existing_path, inserted_path]):
            assert existing_path in sys.path
            assert inserted_path in sys.path

        assert existing_path in sys.path
        assert inserted_path not in sys.path
    finally:
        sys.path[:] = original_sys_path


def test_temporary_sys_path_nested_context_keeps_outer_inserted_path() -> None:
    """Nested contexts with the same path should not break the outer context."""
    shared_path = _unique_sys_path_entry("shared")
    original_sys_path = list(sys.path)

    try:
        with _temporary_sys_path([shared_path]):
            assert shared_path in sys.path

            with _temporary_sys_path([shared_path]):
                assert shared_path in sys.path

            assert shared_path in sys.path

        assert shared_path not in sys.path
    finally:
        sys.path[:] = original_sys_path


def test_temporary_sys_path_overlapping_contexts_keep_path_until_last_exit() -> None:
    """A path should remain available until all overlapping contexts exit."""
    shared_path = _unique_sys_path_entry("overlap")
    original_sys_path = list(sys.path)

    first_context = _temporary_sys_path([shared_path])
    second_context = _temporary_sys_path([shared_path])

    try:
        first_context.__enter__()
        assert shared_path in sys.path

        second_context.__enter__()
        assert shared_path in sys.path

        first_context.__exit__(None, None, None)
        assert shared_path in sys.path

        second_context.__exit__(None, None, None)
        assert shared_path not in sys.path
    finally:
        sys.path[:] = original_sys_path
