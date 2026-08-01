"""Fixtures for executor registry artifact tests."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tracecat.executor import registry_artifact_storage


@pytest.fixture(autouse=True)
def logical_cache_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep synthetic byte budgets independent from host filesystem block sizes."""

    def logical_stat_size(file_stat: os.stat_result) -> int:
        if stat.S_ISDIR(file_stat.st_mode):
            return 0
        return file_stat.st_size

    monkeypatch.setattr(
        registry_artifact_storage,
        "_allocated_stat_size",
        logical_stat_size,
    )
    monkeypatch.setattr(
        registry_artifact_storage,
        "_filesystem_allocation_unit",
        lambda _path: 1,
    )


@pytest.fixture
def temp_cache_dir() -> Iterator[Path]:
    """Create an isolated registry artifact cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
