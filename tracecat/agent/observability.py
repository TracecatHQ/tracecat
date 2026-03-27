"""Process-local LLM gateway observability helpers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class LLMGatewayLoadSnapshot:
    """Snapshot of current process-local LLM gateway path activity."""

    active_connections: int
    active_requests: int
    peak_active_connections: int
    peak_active_requests: int


class LLMGatewayLoadTracker:
    """Tracks process-local LLM gateway connection and request load."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_counter = 0
        self._active_connections = 0
        self._active_requests = 0
        self._peak_active_connections = 0
        self._peak_active_requests = 0

    def begin_connection(self) -> LLMGatewayLoadSnapshot:
        """Record a new downstream connection."""
        with self._lock:
            self._active_connections += 1
            self._peak_active_connections = max(
                self._peak_active_connections, self._active_connections
            )
            return self._snapshot_unlocked()

    def end_connection(self) -> LLMGatewayLoadSnapshot:
        """Record a downstream connection closing."""
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)
            return self._snapshot_unlocked()

    def begin_request(self) -> tuple[int, LLMGatewayLoadSnapshot]:
        """Record a new upstream LLM gateway request."""
        with self._lock:
            self._request_counter += 1
            self._active_requests += 1
            self._peak_active_requests = max(
                self._peak_active_requests, self._active_requests
            )
            return self._request_counter, self._snapshot_unlocked()

    def end_request(self) -> LLMGatewayLoadSnapshot:
        """Record an upstream LLM gateway request finishing."""
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            return self._snapshot_unlocked()

    def snapshot(self) -> LLMGatewayLoadSnapshot:
        """Return the current tracker snapshot."""
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> LLMGatewayLoadSnapshot:
        return LLMGatewayLoadSnapshot(
            active_connections=self._active_connections,
            active_requests=self._active_requests,
            peak_active_connections=self._peak_active_connections,
            peak_active_requests=self._peak_active_requests,
        )


_TRACKERS: dict[str, LLMGatewayLoadTracker] = {}
_TRACKERS_LOCK = Lock()


def get_load_tracker(name: str) -> LLMGatewayLoadTracker:
    """Return a named process-local load tracker."""
    with _TRACKERS_LOCK:
        if name not in _TRACKERS:
            _TRACKERS[name] = LLMGatewayLoadTracker()
        return _TRACKERS[name]
