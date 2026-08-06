"""Uvicorn server variants used by Tracecat-owned supervisors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn


class NoSignalUvicornServer(uvicorn.Server):
    """Uvicorn server that leaves signal handling to its process supervisor."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield
