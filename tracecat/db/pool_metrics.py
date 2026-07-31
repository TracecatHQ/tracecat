"""Low-overhead SQLAlchemy pool metrics for capacity testing.

The recorder is always available to the database engine, but its HTTP endpoint
is disabled unless ``TRACECAT__INTERNAL_DB_POOL_METRICS_PORT`` is positive.
This keeps normal deployments unchanged while allowing diagnostic tooling to
sample every database-owning process independently.
"""

from __future__ import annotations

import json
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final, TypedDict

from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry

POOL_METRICS_PATH: Final = "/db-pool-metrics"
HOLD_STARTED_AT_KEY: Final = "tracecat_pool_metrics_hold_started_at"
CONNECTION_SEEN_KEY: Final = "tracecat_pool_metrics_connection_seen"
LATENCY_BUCKETS_SECONDS: Final = (
    0.0001,
    0.001,
    0.005,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
    5.0,
    10.0,
    30.0,
)


class LatencyMetricsSnapshot(TypedDict):
    """Cumulative latency distribution for one process-local pool."""

    count: int
    sum_seconds: float
    max_seconds: float
    bucket_upper_bounds_seconds: tuple[float, ...]
    cumulative_bucket_counts: tuple[int, ...]


class PoolMetricsSnapshot(TypedDict):
    """Cumulative counters and current gauges for one SQLAlchemy pool."""

    pool: str
    configured_size: int
    configured_max_overflow: int
    configured_timeout_seconds: float
    checkouts_total: int
    connections_created_total: int
    checkout_timeouts_total: int
    returns_total: int
    checked_in: int
    checked_out: int
    overflow: int
    checked_out_high_water: int
    overflow_high_water: int
    checkout_wait: LatencyMetricsSnapshot
    connection_open: LatencyMetricsSnapshot
    connection_hold: LatencyMetricsSnapshot


class PoolMetricsDocument(TypedDict):
    """JSON document served by one Tracecat service process."""

    schema_version: int
    sampled_at_unix_seconds: float
    pools: tuple[PoolMetricsSnapshot, ...]


class _LatencyDistribution:
    """Thread-safe-under-parent-lock cumulative latency distribution."""

    __slots__ = ("_bucket_counts", "_count", "_max_seconds", "_sum_seconds")

    def __init__(self) -> None:
        self._count = 0
        self._sum_seconds = 0.0
        self._max_seconds = 0.0
        self._bucket_counts = [0] * len(LATENCY_BUCKETS_SECONDS)

    def observe(self, seconds: float) -> None:
        duration = max(0.0, seconds)
        self._count += 1
        self._sum_seconds += duration
        self._max_seconds = max(self._max_seconds, duration)
        for index, upper_bound in enumerate(LATENCY_BUCKETS_SECONDS):
            if duration <= upper_bound:
                self._bucket_counts[index] += 1

    def snapshot(self) -> LatencyMetricsSnapshot:
        return LatencyMetricsSnapshot(
            count=self._count,
            sum_seconds=self._sum_seconds,
            max_seconds=self._max_seconds,
            bucket_upper_bounds_seconds=LATENCY_BUCKETS_SECONDS,
            cumulative_bucket_counts=tuple(self._bucket_counts),
        )


class PoolMetricsRecorder:
    """Accumulate checkout and hold measurements for one SQLAlchemy pool."""

    __slots__ = (
        "_checkout_timeouts_total",
        "_checkout_wait",
        "_checkouts_total",
        "_connection_open",
        "_connections_created_total",
        "_connection_hold",
        "_lock",
        "_pool",
        "_pool_name",
        "_returns_total",
        "_checked_out_high_water",
        "_overflow_high_water",
    )

    def __init__(self, pool_name: str, pool: InstrumentedAsyncAdaptedQueuePool) -> None:
        self._pool_name = pool_name
        self._pool = pool
        self._lock = threading.Lock()
        self._checkouts_total = 0
        self._connections_created_total = 0
        self._checkout_timeouts_total = 0
        self._returns_total = 0
        self._checked_out_high_water = 0
        self._overflow_high_water = 0
        self._checkout_wait = _LatencyDistribution()
        self._connection_open = _LatencyDistribution()
        self._connection_hold = _LatencyDistribution()

    def record_checkout(self, acquisition_seconds: float, *, created: bool) -> None:
        with self._lock:
            self._checkouts_total += 1
            if created:
                self._connections_created_total += 1
                self._connection_open.observe(acquisition_seconds)
            else:
                self._checkout_wait.observe(acquisition_seconds)
            self._checked_out_high_water = max(
                self._checked_out_high_water,
                self._pool.checkedout(),
            )
            self._overflow_high_water = max(
                self._overflow_high_water,
                max(0, self._pool.overflow()),
            )

    def record_checkout_timeout(self, wait_seconds: float) -> None:
        with self._lock:
            self._checkout_timeouts_total += 1
            self._checkout_wait.observe(wait_seconds)

    def record_return(self, hold_seconds: float | None) -> None:
        with self._lock:
            self._returns_total += 1
            if hold_seconds is not None and math.isfinite(hold_seconds):
                self._connection_hold.observe(hold_seconds)

    def snapshot(self) -> PoolMetricsSnapshot:
        with self._lock:
            return PoolMetricsSnapshot(
                pool=self._pool_name,
                configured_size=self._pool.size(),
                configured_max_overflow=self._pool._max_overflow,
                configured_timeout_seconds=self._pool.timeout(),
                checkouts_total=self._checkouts_total,
                connections_created_total=self._connections_created_total,
                checkout_timeouts_total=self._checkout_timeouts_total,
                returns_total=self._returns_total,
                checked_in=self._pool.checkedin(),
                checked_out=self._pool.checkedout(),
                overflow=max(0, self._pool.overflow()),
                checked_out_high_water=self._checked_out_high_water,
                overflow_high_water=self._overflow_high_water,
                checkout_wait=self._checkout_wait.snapshot(),
                connection_open=self._connection_open.snapshot(),
                connection_hold=self._connection_hold.snapshot(),
            )


_registry_lock = threading.Lock()
_recorders: dict[str, PoolMetricsRecorder] = {}
_server_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


class InstrumentedAsyncAdaptedQueuePool(AsyncAdaptedQueuePool):
    """Async queue pool that records wait, timeout, occupancy, and hold metrics."""

    def __init__(
        self,
        creator: Any,
        pool_size: int = 5,
        max_overflow: int = 10,
        timeout: float = 30.0,
        use_lifo: bool = False,
        pool_metrics_name: str = "main",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            creator,
            pool_size=pool_size,
            max_overflow=max_overflow,
            timeout=timeout,
            use_lifo=use_lifo,
            **kwargs,
        )
        self._metrics = PoolMetricsRecorder(pool_metrics_name, self)
        with _registry_lock:
            _recorders[pool_metrics_name] = self._metrics

    def _do_get(self) -> ConnectionPoolEntry:
        started_at = time.monotonic()
        try:
            record = super()._do_get()
        except SQLAlchemyTimeoutError:
            self._metrics.record_checkout_timeout(time.monotonic() - started_at)
            raise
        created = not bool(record.info.get(CONNECTION_SEEN_KEY))
        record.info[CONNECTION_SEEN_KEY] = True
        self._metrics.record_checkout(
            time.monotonic() - started_at,
            created=created,
        )
        record.info[HOLD_STARTED_AT_KEY] = time.monotonic()
        return record

    def _do_return_conn(self, record: ConnectionPoolEntry) -> None:
        started_at = record.info.pop(HOLD_STARTED_AT_KEY, None)
        hold_seconds = (
            time.monotonic() - started_at if isinstance(started_at, float) else None
        )
        self._metrics.record_return(hold_seconds)
        super()._do_return_conn(record)


def pool_metrics_document() -> PoolMetricsDocument:
    """Return a consistent process-local snapshot for the collector."""
    with _registry_lock:
        recorders = tuple(_recorders.values())
    return PoolMetricsDocument(
        schema_version=2,
        sampled_at_unix_seconds=time.time(),
        pools=tuple(recorder.snapshot() for recorder in recorders),
    )


class _PoolMetricsRequestHandler(BaseHTTPRequestHandler):
    """Serve the process-local pool snapshot without application dependencies."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != POOL_METRICS_PATH:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = json.dumps(pool_metrics_document(), separators=(",", ":")).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress per-scrape access logs during benchmarks."""
        del format, args


def start_pool_metrics_server(port: int) -> None:
    """Start the load-test-only metrics endpoint once in this process."""
    global _server
    if port <= 0:
        return
    with _server_lock:
        if _server is not None:
            return
        server = ThreadingHTTPServer(("0.0.0.0", port), _PoolMetricsRequestHandler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="tracecat-db-pool-metrics",
            daemon=True,
        )
        thread.start()
        _server = server
