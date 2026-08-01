"""Fixtures for executor registry artifact tests."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def temp_cache_dir() -> Iterator[Path]:
    """Create an isolated registry artifact cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
