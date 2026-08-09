from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest
import uvloop

from tracecat.async_runtime import (
    AppAsyncRuntime,
    AppAsyncRuntimeState,
    use_app_async_runtime,
)
from tracecat.storage import blob as blob_module
from tracecat.storage.backends import s3 as s3_module
from tracecat.storage.backends.s3 import S3ObjectStorage
from tracecat.storage.blob import get_storage_client
from tracecat.storage.object import ExternalObject


def test_app_async_runtime_collapses_64_callers_onto_one_s3_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All activity threads reuse one loop-owned entered S3 client."""
    caller_count = 64
    start_barrier = threading.Barrier(caller_count + 1)
    observed_loop_ids: set[int] = set()
    observed_loop_types: set[type[asyncio.AbstractEventLoop]] = set()
    observed_thread_ids: set[int] = set()
    observation_lock = threading.Lock()
    runtime = AppAsyncRuntime(
        name="test-app-io",
        loop_factory=uvloop.new_event_loop,
    )

    blob_module.clear_storage_session_cache()
    monkeypatch.setattr(
        blob_module.config,
        "TRACECAT__BLOB_STORAGE_ENDPOINT",
        None,
        raising=False,
    )

    async def use_client() -> object:
        with observation_lock:
            loop = asyncio.get_running_loop()
            observed_loop_ids.add(id(loop))
            observed_loop_types.add(type(loop))
            observed_thread_ids.add(threading.get_ident())
        async with get_storage_client() as client:
            await asyncio.sleep(0)
            return client

    def caller() -> object:
        start_barrier.wait(timeout=10)
        return runtime.run_sync(use_client())

    with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
        mock_session = mock_session_cls.return_value
        mock_client = AsyncMock()
        mock_session.client.return_value.__aenter__.return_value = mock_client

        runtime.start()
        try:
            with ThreadPoolExecutor(max_workers=caller_count) as executor:
                futures = [executor.submit(caller) for _ in range(caller_count)]
                start_barrier.wait(timeout=10)
                clients = [future.result(timeout=10) for future in futures]
        finally:
            runtime.close(cleanup=blob_module.close_storage_client_cache)

        assert clients == [mock_client] * caller_count
        assert len(observed_loop_ids) == 1
        assert observed_loop_types == {uvloop.Loop}
        assert len(observed_thread_ids) == 1
        mock_session_cls.assert_called_once()
        mock_session.client.return_value.__aenter__.assert_awaited_once()
        mock_session.client.return_value.__aexit__.assert_awaited_once_with(
            None, None, None
        )
        assert len(blob_module._STORAGE_CLIENTS) == 0


def test_sync_storage_keeps_cpu_on_activity_thread_and_io_on_uvloop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only S3 coroutines cross from an activity thread to the app loop."""
    runtime = AppAsyncRuntime(
        name="test-app-io",
        loop_factory=uvloop.new_event_loop,
    )
    storage = S3ObjectStorage(bucket="bucket", threshold_bytes=0)
    cpu_thread_ids: list[int] = []
    io_thread_ids: list[int] = []
    io_loop_types: list[type[asyncio.AbstractEventLoop]] = []
    original_prepare = s3_module._prepare_payload
    original_verify = s3_module._verify_and_deserialize

    def prepare(data: object) -> s3_module._PreparedPayload:
        cpu_thread_ids.append(threading.get_ident())
        return original_prepare(data)

    def verify(content: bytes, *, expected_sha256: str, key: str) -> object:
        cpu_thread_ids.append(threading.get_ident())
        return original_verify(content, expected_sha256=expected_sha256, key=key)

    async def record_io(*_args: object, **_kwargs: object) -> None:
        io_thread_ids.append(threading.get_ident())
        io_loop_types.append(type(asyncio.get_running_loop()))

    prepared = original_prepare({"payload": "value"})

    async def download(**_kwargs: object) -> bytes:
        io_thread_ids.append(threading.get_ident())
        io_loop_types.append(type(asyncio.get_running_loop()))
        return prepared.content

    monkeypatch.setattr(s3_module, "_prepare_payload", prepare)
    monkeypatch.setattr(s3_module, "_verify_and_deserialize", verify)
    monkeypatch.setattr(blob_module, "ensure_bucket_exists", record_io)
    monkeypatch.setattr(blob_module, "upload_file", record_io)
    monkeypatch.setattr(s3_module, "cached_blob_download", download)

    def activity_work() -> tuple[int, object]:
        activity_thread_id = threading.get_ident()
        stored = storage.store_sync("key", {"payload": "value"})
        assert isinstance(stored, ExternalObject)
        return activity_thread_id, storage.retrieve_sync(stored)

    runtime.start()
    try:
        with use_app_async_runtime(runtime):
            with ThreadPoolExecutor(max_workers=1) as executor:
                activity_thread_id, retrieved = executor.submit(activity_work).result(
                    timeout=10
                )
    finally:
        runtime.close()

    assert retrieved == {"payload": "value"}
    assert cpu_thread_ids == [activity_thread_id, activity_thread_id]
    assert io_thread_ids == [runtime.thread_id, runtime.thread_id, runtime.thread_id]
    assert io_loop_types == [uvloop.Loop, uvloop.Loop, uvloop.Loop]


def test_app_async_runtime_preserves_context_and_exceptions() -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    request_id = contextvars.ContextVar("request_id", default="missing")

    async def read_context() -> str:
        return request_id.get()

    async def raise_error() -> None:
        raise LookupError("preserved")

    async def raise_timeout_error() -> None:
        raise TimeoutError("from coroutine")

    runtime.start()
    try:
        token = request_id.set("activity-request")
        try:
            assert runtime.run_sync(read_context()) == "activity-request"
        finally:
            request_id.reset(token)

        with pytest.raises(LookupError, match="preserved"):
            runtime.run_sync(raise_error())
        with pytest.raises(TimeoutError, match="from coroutine"):
            runtime.run_sync(raise_timeout_error())
    finally:
        runtime.close()


@pytest.mark.anyio
async def test_app_async_runtime_async_bridge_preserves_context_and_exceptions() -> (
    None
):
    runtime = AppAsyncRuntime(name="test-app-io")
    request_id = contextvars.ContextVar("request_id", default="missing")

    async def read_context() -> str:
        return request_id.get()

    async def raise_error() -> None:
        raise LookupError("preserved")

    runtime.start()
    try:
        token = request_id.set("temporal-request")
        try:
            assert await runtime.run_async(read_context()) == "temporal-request"
        finally:
            request_id.reset(token)

        with pytest.raises(LookupError, match="preserved"):
            await runtime.run_async(raise_error())
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_blob_operations_route_off_the_temporal_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    caller_loop = asyncio.get_running_loop()
    operation_loop_ids: list[int] = []
    operation_thread_ids: list[int] = []

    blob_module.clear_storage_session_cache()
    monkeypatch.setattr(
        blob_module.config,
        "TRACECAT__BLOB_STORAGE_ENDPOINT",
        None,
        raising=False,
    )

    with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
        mock_session = mock_session_cls.return_value
        mock_client = AsyncMock()

        async def put_object(**_kwargs: object) -> None:
            operation_loop_ids.append(id(asyncio.get_running_loop()))
            operation_thread_ids.append(threading.get_ident())

        mock_client.put_object.side_effect = put_object
        mock_session.client.return_value.__aenter__.return_value = mock_client

        runtime.start()
        try:
            with use_app_async_runtime(runtime):
                await blob_module.upload_file(b"one", "one", "bucket")
                await blob_module.upload_file(b"two", "two", "bucket")
        finally:
            await runtime.aclose(cleanup=blob_module.close_storage_client_cache)

    assert operation_loop_ids == [operation_loop_ids[0], operation_loop_ids[0]]
    assert operation_loop_ids[0] != id(caller_loop)
    assert operation_thread_ids == [runtime.thread_id, runtime.thread_id]
    mock_session_cls.assert_called_once()
    mock_session.client.return_value.__aenter__.assert_awaited_once()


@pytest.mark.anyio
async def test_app_async_runtime_async_cancellation_cancels_submitted_task() -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    started = threading.Event()
    cancelled = threading.Event()

    async def wait_forever() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime.start()
    try:
        task = asyncio.create_task(runtime.run_async(wait_forever()))
        assert await asyncio.to_thread(started.wait, 5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await asyncio.to_thread(cancelled.wait, 5)
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_app_async_runtime_drains_cancelled_task_before_cleanup() -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    started = threading.Event()
    finalizing = threading.Event()
    release_finalizer = threading.Event()
    cleanup_called = threading.Event()

    async def cancellable_work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await asyncio.to_thread(release_finalizer.wait)

    async def cleanup() -> None:
        cleanup_called.set()

    runtime.start()
    caller_task = asyncio.create_task(runtime.run_async(cancellable_work()))
    assert await asyncio.to_thread(started.wait, 5)
    caller_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller_task
    assert await asyncio.to_thread(finalizing.wait, 5)

    close_task = asyncio.create_task(runtime.aclose(cleanup=cleanup))
    await asyncio.sleep(0.05)
    assert cleanup_called.is_set() is False

    release_finalizer.set()
    await close_task
    assert cleanup_called.is_set() is True
    assert runtime.health.pending_submissions == 0


def test_app_async_runtime_sync_interruption_cancels_submitted_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    submitted: Future[None] = Future()
    result_calls = 0

    def interrupt_result(*_args: object, **_kwargs: object) -> None:
        nonlocal result_calls
        result_calls += 1
        if result_calls == 1:
            raise TimeoutError
        raise KeyboardInterrupt

    monkeypatch.setattr(submitted, "result", interrupt_result)
    monkeypatch.setattr(runtime, "_submit", lambda _coro: submitted)
    coroutine = asyncio.sleep(0)
    with pytest.raises(KeyboardInterrupt):
        runtime.run_sync(coroutine)

    assert submitted.cancelled()
    assert result_calls == 2
    coroutine.close()


@pytest.mark.anyio
async def test_app_async_runtime_rejects_blocking_from_event_loop_threads() -> None:
    runtime = AppAsyncRuntime(name="test-app-io")
    runtime.start()
    try:
        with pytest.raises(RuntimeError, match="event-loop thread"):
            runtime.run_sync(asyncio.sleep(0))

        async def block_from_app_loop() -> None:
            with pytest.raises(RuntimeError, match="event-loop thread"):
                runtime.run_sync(asyncio.sleep(0))

        await runtime.run_async(block_from_app_loop())
    finally:
        await runtime.aclose()


def test_app_async_runtime_reports_readiness_and_stops_intake() -> None:
    runtime = AppAsyncRuntime(name="test-app-io")

    assert runtime.state is AppAsyncRuntimeState.NEW
    assert runtime.is_healthy is False

    runtime.start()
    health = runtime.health
    assert health.state is AppAsyncRuntimeState.RUNNING
    assert health.ready is True
    assert health.thread_alive is True
    assert health.loop_running is True
    assert runtime.is_healthy is True

    runtime.close()
    assert runtime.state is AppAsyncRuntimeState.STOPPED
    assert runtime.is_healthy is False

    coroutine = asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="not accepting submissions"):
        runtime.run_sync(coroutine)


def test_app_async_runtime_reports_loop_start_failure() -> None:
    def fail_loop_creation() -> asyncio.AbstractEventLoop:
        raise LookupError("loop factory failed")

    runtime = AppAsyncRuntime(loop_factory=fail_loop_creation)

    with pytest.raises(RuntimeError, match="failed to start") as exc_info:
        runtime.start()

    assert isinstance(exc_info.value.__cause__, LookupError)
    assert runtime.state is AppAsyncRuntimeState.FAILED
    assert runtime.health.thread_alive is False


def test_app_async_runtime_failed_close_cancels_stranded_submissions() -> None:
    """A dead loop must not leave close() waiting forever on orphaned Futures."""
    runtime = AppAsyncRuntime(name="test-app-io")
    stranded: Future[None] = Future()

    runtime.start()
    with runtime._lock:
        runtime._pending.add(stranded)
    runtime_loop = runtime._loop
    runtime_thread = runtime._thread
    assert runtime_loop is not None
    assert runtime_thread is not None

    runtime_loop.call_soon_threadsafe(runtime_loop.stop)
    runtime_thread.join(timeout=5)
    assert runtime_thread.is_alive() is False
    assert runtime.state is AppAsyncRuntimeState.FAILED

    # Numeric thread identifiers may be reused after the owner thread exits.
    # close() must compare Thread objects instead of rejecting this caller.
    with runtime._lock:
        runtime._thread_id = threading.get_ident()

    with pytest.raises(RuntimeError, match="failed during shutdown") as exc_info:
        runtime.close(timeout=1)

    assert stranded.cancelled() is True
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == (
        "App async runtime loop stopped outside close()"
    )
