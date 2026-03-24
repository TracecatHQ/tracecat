"""Process-local LiteLLM path observability helpers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class LiteLLMLoadSnapshot:
    """Snapshot of current process-local LiteLLM path activity."""

    active_connections: int
    active_requests: int
    peak_active_connections: int
    peak_active_requests: int


class LiteLLMLoadTracker:
    """Tracks process-local LiteLLM-related connection and request load."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_counter = 0
        self._active_connections = 0
        self._active_requests = 0
        self._peak_active_connections = 0
        self._peak_active_requests = 0

    def begin_connection(self) -> LiteLLMLoadSnapshot:
        """Record a new downstream connection."""
        with self._lock:
            self._active_connections += 1
            self._peak_active_connections = max(
                self._peak_active_connections, self._active_connections
            )
            return self._snapshot_unlocked()

    def end_connection(self) -> LiteLLMLoadSnapshot:
        """Record a downstream connection closing."""
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)
            return self._snapshot_unlocked()

    def begin_request(self) -> tuple[int, LiteLLMLoadSnapshot]:
        """Record a new upstream LiteLLM request."""
        with self._lock:
            self._request_counter += 1
            self._active_requests += 1
            self._peak_active_requests = max(
                self._peak_active_requests, self._active_requests
            )
            return self._request_counter, self._snapshot_unlocked()

    def end_request(self) -> LiteLLMLoadSnapshot:
        """Record an upstream LiteLLM request finishing."""
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            return self._snapshot_unlocked()

    def snapshot(self) -> LiteLLMLoadSnapshot:
        """Return the current tracker snapshot."""
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> LiteLLMLoadSnapshot:
        return LiteLLMLoadSnapshot(
            active_connections=self._active_connections,
            active_requests=self._active_requests,
            peak_active_connections=self._peak_active_connections,
            peak_active_requests=self._peak_active_requests,
        )


_TRACKERS: dict[str, LiteLLMLoadTracker] = {}
_TRACKERS_LOCK = Lock()


def get_load_tracker(name: str) -> LiteLLMLoadTracker:
    """Return a named process-local load tracker."""
    with _TRACKERS_LOCK:
        if name not in _TRACKERS:
            _TRACKERS[name] = LiteLLMLoadTracker()
        return _TRACKERS[name]
