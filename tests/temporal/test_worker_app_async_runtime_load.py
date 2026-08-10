"""Release-gate load test for the worker-owned application I/O loop.

The checked-in PR profile warms all 64 Temporal activity threads with one
warm-up wave and three measured 2 MiB waves. Set
``TRACECAT_TEST_APP_IO_SOAK_SECONDS=900`` to use 4 MiB payloads and extend the
run to at least twenty measured waves for the pre-rollout soak profile. Set
``TRACECAT_TEST_APP_IO_METRICS_PATH`` to retain the JSON metrics artifact.
"""

from __future__ import annotations

import asyncio
import gc
import os
import platform
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import orjson
import pytest
import uvloop
from minio import Minio
from minio.deleteobjects import DeleteObject
from pydantic import BaseModel

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.regression,
    pytest.mark.skipif(
        platform.system() != "Linux",
        reason="The app I/O release gate requires Linux /proc metrics",
    ),
]

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tracecat import config
from tracecat.async_runtime import (
    AppAsyncRuntime,
    get_app_async_runtime,
    run_sync,
    use_app_async_runtime,
)
from tracecat.dsl.action import materialize_context_sync
from tracecat.dsl.schemas import ExecutionContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.storage import blob
from tracecat.storage.backends.s3 import S3ObjectStorage
from tracecat.storage.object import ExternalObject, ObjectRef
from tracecat.storage.utils import clear_blob_cache, compute_sha256, serialize_object

DEFAULT_CALLER_COUNT = 64
DEFAULT_PAYLOAD_SIZE_BYTES = 2 * 1024 * 1024
SOAK_PAYLOAD_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_EXPRESSION_COUNT = 128
DEFAULT_MEASURED_WAVES = 3
SOAK_MINIMUM_WAVES = 20
CANARY_POLL_SECONDS = 0.25
CANARY_MAX_STALL_SECONDS = 1.5
WAVE_TIMEOUT_SECONDS = 300
RSS_PLATEAU_MIN_ALLOWANCE_BYTES = 32 * 1024 * 1024


class _NestedPayload(BaseModel):
    value: str


class _LoadPayload(BaseModel):
    caller_index: int
    wave_index: int
    nested: _NestedPayload


class _RenderedPayload(BaseModel):
    caller_index: int
    wave_index: int
    rendered: str
    expressions: dict[str, int]


class _MixedLoadActivityInput(BaseModel):
    barrier_id: str
    source: ExternalObject
    output_key: str
    expression_count: int


class _MixedLoadActivityResult(BaseModel):
    caller_index: int
    wave_index: int
    output_sha256: str
    activity_thread_id: int
    app_thread_id: int


class _MixedLoadWorkflowInput(BaseModel):
    activities: list[_MixedLoadActivityInput]


class _WaveMetrics(BaseModel):
    wave_index: int
    measured: bool
    rss_bytes: int
    fd_count: int
    thread_count: int
    app_loop_thread_count: int
    app_pending_submissions: int
    storage_loop_count: int
    entered_storage_client_count: int
    canary_ticks: int


class _LoadMetricsArtifact(BaseModel):
    caller_count: int
    payload_size_bytes: int
    expression_count: int
    measured_wave_target: int
    soak_seconds: float
    rss_before_load_bytes: int
    peak_rss_bytes: int
    max_canary_query_seconds: float
    waves: list[_WaveMetrics]


@dataclass(frozen=True, slots=True)
class _PreparedSeedObject:
    activity_input: _MixedLoadActivityInput
    content: bytes


_LOAD_BARRIERS: dict[str, threading.Barrier] = {}
_LOAD_BARRIERS_LOCK = threading.RLock()


def _register_load_barrier(barrier_id: str, caller_count: int) -> None:
    with _LOAD_BARRIERS_LOCK:
        _LOAD_BARRIERS[barrier_id] = threading.Barrier(caller_count)


def _remove_load_barrier(barrier_id: str) -> None:
    with _LOAD_BARRIERS_LOCK:
        _LOAD_BARRIERS.pop(barrier_id, None)


def _wait_for_load_barrier(barrier_id: str) -> None:
    with _LOAD_BARRIERS_LOCK:
        barrier = _LOAD_BARRIERS[barrier_id]
    barrier.wait(timeout=60)


@activity.defn
def mixed_storage_cpu_load_activity(
    input: _MixedLoadActivityInput,
) -> _MixedLoadActivityResult:
    """Exercise materialization, expressions, rendering, hashing, and S3."""
    activity.heartbeat("waiting-for-wave")
    _wait_for_load_barrier(input.barrier_id)
    activity.heartbeat("wave-started")

    activity_thread_id = threading.get_ident()
    context = ExecutionContext(ACTIONS={}, TRIGGER=input.source)
    materialized = materialize_context_sync(context)
    trigger_value = materialized.get("TRIGGER")
    if trigger_value is None:
        raise ValueError("Materialized context did not contain trigger data")
    trigger = _LoadPayload.model_validate(trigger_value)

    expression_template = {
        f"expression_{index}": "${{ TRIGGER.caller_index }}"
        for index in range(input.expression_count)
    }
    rendered = _RenderedPayload.model_validate(
        eval_templated_object(
            {
                "caller_index": "${{ TRIGGER.caller_index }}",
                "wave_index": "${{ TRIGGER.wave_index }}",
                "rendered": "payload:${{ TRIGGER.nested.value }}",
                "expressions": expression_template,
            },
            operand=materialized,
        )
    )
    if set(rendered.expressions.values()) != {trigger.caller_index}:
        raise ValueError("Expression resolution returned an unexpected value")

    # This intentionally stays in the synchronous activity thread. Only the
    # upload/download coroutine crosses onto the app I/O loop.
    output_bytes = serialize_object(rendered.model_dump())
    output_sha256 = compute_sha256(output_bytes)
    bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW
    run_sync(
        blob.upload_file(
            content=output_bytes,
            key=input.output_key,
            bucket=bucket,
            content_type="application/json",
        )
    )
    activity.heartbeat("output-uploaded")

    stored_output = ExternalObject(
        ref=ObjectRef(
            backend="s3",
            bucket=bucket,
            key=input.output_key,
            size_bytes=len(output_bytes),
            sha256=output_sha256,
            content_type="application/json",
            encoding="json",
        ),
        typename=_RenderedPayload.__name__,
    )
    retrieved = _RenderedPayload.model_validate(
        S3ObjectStorage(bucket=bucket).retrieve_sync(stored_output)
    )
    if retrieved != rendered:
        raise ValueError("Retrieved output did not match the rendered result")

    runtime = get_app_async_runtime()
    if runtime is None or runtime.thread_id is None:
        raise RuntimeError("App async runtime is not healthy")
    return _MixedLoadActivityResult(
        caller_index=trigger.caller_index,
        wave_index=trigger.wave_index,
        output_sha256=output_sha256,
        activity_thread_id=activity_thread_id,
        app_thread_id=runtime.thread_id,
    )


@workflow.defn
class MixedStorageCpuLoadWorkflow:
    @workflow.run
    async def run(
        self, input: _MixedLoadWorkflowInput
    ) -> list[_MixedLoadActivityResult]:
        return await asyncio.gather(
            *(
                workflow.execute_activity(
                    mixed_storage_cpu_load_activity,
                    activity_input,
                    start_to_close_timeout=timedelta(seconds=WAVE_TIMEOUT_SECONDS),
                    heartbeat_timeout=timedelta(seconds=30),
                )
                for activity_input in input.activities
            )
        )


@workflow.defn
class TemporalLoopCanaryWorkflow:
    def __init__(self) -> None:
        self._ticks = 0
        self._stopped = False

    @workflow.run
    async def run(self) -> int:
        while not self._stopped:
            await workflow.sleep(CANARY_POLL_SECONDS)
            self._ticks += 1
        return self._ticks

    @workflow.signal
    def stop(self) -> None:
        self._stopped = True

    @workflow.query
    def progress(self) -> int:
        return self._ticks


def _linux_process_metrics() -> tuple[int, int, int]:
    page_size = os.sysconf("SC_PAGE_SIZE")
    resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
    rss_bytes = resident_pages * page_size
    fd_count = len(list(Path("/proc/self/fd").iterdir()))
    thread_count = len(list(Path("/proc/self/task").iterdir()))
    return rss_bytes, fd_count, thread_count


def _delete_test_objects(client: Minio, *, bucket: str, prefix: str) -> None:
    object_names = [
        item.object_name
        for item in client.list_objects(bucket, prefix=prefix, recursive=True)
        if item.object_name is not None
    ]
    objects = (DeleteObject(name) for name in object_names)
    errors = list(client.remove_objects(bucket, objects))
    if errors:
        raise AssertionError(f"Failed to remove {len(errors)} load-test objects")


def _load_profile() -> tuple[int, int, int, int, float]:
    soak_seconds = float(os.environ.get("TRACECAT_TEST_APP_IO_SOAK_SECONDS") or 0)
    caller_count = int(
        os.environ.get("TRACECAT_TEST_APP_IO_CALLERS") or DEFAULT_CALLER_COUNT
    )
    payload_size_bytes = int(
        os.environ.get("TRACECAT_TEST_APP_IO_PAYLOAD_BYTES")
        or (SOAK_PAYLOAD_SIZE_BYTES if soak_seconds > 0 else DEFAULT_PAYLOAD_SIZE_BYTES)
    )
    expression_count = int(
        os.environ.get("TRACECAT_TEST_APP_IO_EXPRESSIONS") or DEFAULT_EXPRESSION_COUNT
    )
    measured_waves = int(
        os.environ.get("TRACECAT_TEST_APP_IO_MEASURED_WAVES") or DEFAULT_MEASURED_WAVES
    )
    if soak_seconds > 0:
        measured_waves = max(measured_waves, SOAK_MINIMUM_WAVES)
    if caller_count != DEFAULT_CALLER_COUNT and soak_seconds == 0:
        raise ValueError("The checked-in PR profile must warm all 64 activity threads")
    if not 100 <= expression_count <= 250:
        raise ValueError("Expression count must stay between 100 and 250")
    return (
        caller_count,
        payload_size_bytes,
        expression_count,
        measured_waves,
        soak_seconds,
    )


async def _seed_wave(
    *,
    wave_index: int,
    caller_count: int,
    payload_size_bytes: int,
    expression_count: int,
    test_run_id: str,
) -> list[_MixedLoadActivityInput]:
    bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW
    barrier_id = f"{test_run_id}-wave-{wave_index}"
    prepared = await asyncio.to_thread(
        _prepare_seed_wave,
        wave_index=wave_index,
        caller_count=caller_count,
        payload_size_bytes=payload_size_bytes,
        expression_count=expression_count,
        test_run_id=test_run_id,
        barrier_id=barrier_id,
        bucket=bucket,
    )
    uploads: list[asyncio.Task[None]] = []
    for seed_object in prepared:
        uploads.append(
            asyncio.create_task(
                blob.upload_file(
                    content=seed_object.content,
                    key=seed_object.activity_input.source.ref.key,
                    bucket=bucket,
                    content_type="application/json",
                )
            )
        )

    await asyncio.gather(*uploads)
    return [seed_object.activity_input for seed_object in prepared]


def _prepare_seed_wave(
    *,
    wave_index: int,
    caller_count: int,
    payload_size_bytes: int,
    expression_count: int,
    test_run_id: str,
    barrier_id: str,
    bucket: str,
) -> list[_PreparedSeedObject]:
    """Build load payloads without blocking the Temporal worker loop."""
    prepared: list[_PreparedSeedObject] = []
    for caller_index in range(caller_count):
        sentinel = f"{test_run_id}:{wave_index}:{caller_index}:"
        padding_size = max(1, payload_size_bytes - len(sentinel))
        payload = _LoadPayload(
            caller_index=caller_index,
            wave_index=wave_index,
            nested=_NestedPayload(value=sentinel + ("x" * padding_size)),
        )
        content = serialize_object(payload.model_dump())
        source_key = (
            f"tests/app-io-load/{test_run_id}/wave-{wave_index}/"
            f"source-{caller_index}.json"
        )
        prepared.append(
            _PreparedSeedObject(
                activity_input=_MixedLoadActivityInput(
                    barrier_id=barrier_id,
                    source=ExternalObject(
                        ref=ObjectRef(
                            backend="s3",
                            bucket=bucket,
                            key=source_key,
                            size_bytes=len(content),
                            sha256=compute_sha256(content),
                            content_type="application/json",
                            encoding="json",
                        ),
                        typename=_LoadPayload.__name__,
                    ),
                    output_key=(
                        f"tests/app-io-load/{test_run_id}/wave-{wave_index}/"
                        f"output-{caller_index}.json"
                    ),
                    expression_count=expression_count,
                ),
                content=content,
            )
        )
    return prepared


async def _monitor_canary(
    handle: Any,
    *,
    stop: asyncio.Event,
    query_durations: list[float],
) -> None:
    previous_ticks = -1
    last_progress_at = time.monotonic()
    while not stop.is_set():
        query_started_at = time.monotonic()
        ticks = await handle.query(TemporalLoopCanaryWorkflow.progress)
        query_duration = time.monotonic() - query_started_at
        query_durations.append(query_duration)
        if query_duration >= CANARY_MAX_STALL_SECONDS:
            raise AssertionError(
                "Temporal workflow canary query approached the deadlock threshold"
            )
        if ticks > previous_ticks:
            previous_ticks = ticks
            last_progress_at = time.monotonic()
        elif time.monotonic() - last_progress_at >= CANARY_MAX_STALL_SECONDS:
            raise AssertionError(
                "Temporal workflow canary stopped progressing during mixed load"
            )
        await asyncio.sleep(CANARY_POLL_SECONDS)


async def _monitor_peak_rss(
    samples: list[int],
    *,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        samples.append(_linux_process_metrics()[0])
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1)
        except TimeoutError:
            pass


@pytest.mark.anyio
async def test_worker_app_io_runtime_mixed_cpu_s3_load_plateaus(
    temporal_client: Client,
    minio_client: Minio,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warm 64 activity threads while one uvloop/client serves all S3 I/O."""
    (
        caller_count,
        payload_size_bytes,
        expression_count,
        measured_wave_target,
        soak_seconds,
    ) = _load_profile()
    access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret_key)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    test_run_id = uuid.uuid4().hex
    task_queue = f"app-io-load-{test_run_id}"
    app_runtime = AppAsyncRuntime(
        name="test-worker-app-io",
        loop_factory=uvloop.new_event_loop,
    )
    app_runtime.start()
    wave_metrics: list[_WaveMetrics] = []
    canary_ticks = 0
    canary_monitor_stop = asyncio.Event()
    rss_monitor_stop = asyncio.Event()
    rss_samples: list[int] = []
    canary_query_durations: list[float] = []
    rss_before_load_bytes = 0
    metrics_path = Path(
        os.environ.get("TRACECAT_TEST_APP_IO_METRICS_PATH")
        or tmp_path / "app-io-load-metrics.json"
    )

    try:
        with use_app_async_runtime(app_runtime):
            await blob.close_storage_client_cache()
            await clear_blob_cache()
            with ThreadPoolExecutor(max_workers=caller_count) as activity_executor:
                async with Worker(
                    temporal_client,
                    task_queue=task_queue,
                    workflows=[
                        MixedStorageCpuLoadWorkflow,
                        TemporalLoopCanaryWorkflow,
                    ],
                    activities=[mixed_storage_cpu_load_activity],
                    activity_executor=activity_executor,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    max_concurrent_activities=caller_count,
                    max_concurrent_workflow_tasks=16,
                    graceful_shutdown_timeout=timedelta(seconds=30),
                ):
                    canary_handle = await temporal_client.start_workflow(
                        TemporalLoopCanaryWorkflow.run,
                        id=f"app-io-canary-{test_run_id}",
                        task_queue=task_queue,
                    )
                    canary_monitor = asyncio.create_task(
                        _monitor_canary(
                            canary_handle,
                            stop=canary_monitor_stop,
                            query_durations=canary_query_durations,
                        )
                    )
                    rss_before_load_bytes = _linux_process_metrics()[0]
                    rss_samples.append(rss_before_load_bytes)
                    rss_monitor = asyncio.create_task(
                        _monitor_peak_rss(rss_samples, stop=rss_monitor_stop)
                    )
                    measured_wave_count = 0
                    wave_index = 0
                    measured_started_at: float | None = None
                    try:
                        while True:
                            measured = wave_index > 0
                            if measured and measured_started_at is None:
                                measured_started_at = time.monotonic()
                            inputs = await _seed_wave(
                                wave_index=wave_index,
                                caller_count=caller_count,
                                payload_size_bytes=payload_size_bytes,
                                expression_count=expression_count,
                                test_run_id=test_run_id,
                            )
                            barrier_id = inputs[0].barrier_id
                            _register_load_barrier(barrier_id, caller_count)
                            load_handle = await temporal_client.start_workflow(
                                MixedStorageCpuLoadWorkflow.run,
                                _MixedLoadWorkflowInput(activities=inputs),
                                id=f"app-io-load-{test_run_id}-{wave_index}",
                                task_queue=task_queue,
                            )
                            try:
                                raw_results = await asyncio.wait_for(
                                    load_handle.result(),
                                    timeout=WAVE_TIMEOUT_SECONDS,
                                )
                            except BaseException:
                                await load_handle.cancel()
                                raise
                            finally:
                                _remove_load_barrier(barrier_id)

                            results = [
                                _MixedLoadActivityResult.model_validate(result)
                                for result in raw_results
                            ]
                            assert len(results) == caller_count
                            assert {result.caller_index for result in results} == set(
                                range(caller_count)
                            )
                            assert {result.wave_index for result in results} == {
                                wave_index
                            }
                            assert (
                                len({result.output_sha256 for result in results})
                                == caller_count
                            )
                            assert (
                                len({result.activity_thread_id for result in results})
                                == caller_count
                            )
                            assert {result.app_thread_id for result in results} == {
                                app_runtime.thread_id
                            }
                            if canary_monitor.done():
                                await canary_monitor

                            # Force every wave through MinIO while excluding the
                            # intentionally bounded 500 MiB byte cache from the
                            # retained-RSS plateau signal under test.
                            await clear_blob_cache()
                            await asyncio.sleep(0.5)
                            await asyncio.to_thread(gc.collect)
                            rss_bytes, fd_count, thread_count = _linux_process_metrics()
                            runtime_health = app_runtime.health
                            storage_health = blob.storage_client_cache_health()
                            ticks = await canary_handle.query(
                                TemporalLoopCanaryWorkflow.progress
                            )
                            app_loop_thread_count = sum(
                                thread.name == "test-worker-app-io"
                                and thread.is_alive()
                                for thread in threading.enumerate()
                            )
                            wave_metrics.append(
                                _WaveMetrics(
                                    wave_index=wave_index,
                                    measured=measured,
                                    rss_bytes=rss_bytes,
                                    fd_count=fd_count,
                                    thread_count=thread_count,
                                    app_loop_thread_count=app_loop_thread_count,
                                    app_pending_submissions=(
                                        runtime_health.pending_submissions
                                    ),
                                    storage_loop_count=storage_health.loop_count,
                                    entered_storage_client_count=(
                                        storage_health.entered_client_count
                                    ),
                                    canary_ticks=ticks,
                                )
                            )
                            assert runtime_health.pending_submissions == 0
                            assert app_loop_thread_count == 1
                            assert storage_health.loop_count == 1
                            assert storage_health.entered_client_count == 1
                            assert storage_health.active_user_count == 0

                            if measured:
                                measured_wave_count += 1
                            wave_index += 1
                            measured_elapsed = (
                                time.monotonic() - measured_started_at
                                if measured_started_at is not None
                                else 0.0
                            )
                            if (
                                measured_wave_count >= measured_wave_target
                                and measured_elapsed >= soak_seconds
                            ):
                                break
                    finally:
                        canary_monitor_stop.set()
                        rss_monitor_stop.set()
                        await rss_monitor
                        await canary_handle.signal(TemporalLoopCanaryWorkflow.stop)
                        canary_ticks = await canary_handle.result()
                        await canary_monitor
    finally:
        try:
            try:
                await app_runtime.aclose(cleanup=blob.close_storage_client_cache)
            finally:
                await clear_blob_cache()
                await asyncio.to_thread(
                    _delete_test_objects,
                    minio_client,
                    bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
                    prefix=f"tests/app-io-load/{test_run_id}/",
                )
        finally:
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_bytes(
                orjson.dumps(
                    _LoadMetricsArtifact(
                        caller_count=caller_count,
                        payload_size_bytes=payload_size_bytes,
                        expression_count=expression_count,
                        measured_wave_target=measured_wave_target,
                        soak_seconds=soak_seconds,
                        rss_before_load_bytes=rss_before_load_bytes,
                        peak_rss_bytes=max(rss_samples, default=0),
                        max_canary_query_seconds=max(
                            canary_query_durations,
                            default=0.0,
                        ),
                        waves=wave_metrics,
                    ).model_dump(),
                    option=orjson.OPT_INDENT_2,
                )
            )

    assert app_runtime.health.pending_submissions == 0
    assert app_runtime.health.thread_alive is False
    assert not any(
        thread.name == "test-worker-app-io" and thread.is_alive()
        for thread in threading.enumerate()
    )
    storage_health = blob.storage_client_cache_health()
    assert storage_health.loop_count == 0
    assert storage_health.entered_client_count == 0
    assert canary_ticks > 0
    assert canary_query_durations
    assert max(canary_query_durations) < CANARY_MAX_STALL_SECONDS

    measured_metrics = [metrics for metrics in wave_metrics if metrics.measured]
    assert len(measured_metrics) >= measured_wave_target
    assert all(
        metrics.fd_count <= measured_metrics[0].fd_count
        for metrics in measured_metrics[1:]
    )
    assert all(
        metrics.thread_count <= measured_metrics[0].thread_count
        for metrics in measured_metrics[1:]
    )
    rss_allowance = max(
        RSS_PLATEAU_MIN_ALLOWANCE_BYTES,
        measured_metrics[0].rss_bytes // 10,
    )
    assert (
        measured_metrics[-1].rss_bytes - measured_metrics[0].rss_bytes <= rss_allowance
    )
    assert all(
        later.canary_ticks > earlier.canary_ticks
        for earlier, later in zip(
            measured_metrics,
            measured_metrics[1:],
            strict=False,
        )
    )

    assert metrics_path.stat().st_size > 0

    captured = capsys.readouterr()
    worker_output = f"{caplog.text}\n{captured.out}\n{captured.err}"
    for forbidden in ("TMPRL1101", "Potential deadlock", "didn't yield"):
        assert forbidden not in worker_output
