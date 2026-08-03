"""Temporal-worker concurrency coverage for the registry artifact cache."""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    compute_registry_artifact_cache_key,
)

_PROBE_ACTIVITY_NAME = "registry_artifact_cache_concurrency_probe"


@pytest.fixture
async def temporal_env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Run this worker test against Temporal's self-contained test server."""
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


@dataclass(frozen=True, slots=True)
class _ProbeResult:
    """Runtime identity observed by one Temporal activity."""

    cache_instance_id: int
    event_loop_id: int
    thread_id: int
    registry_path: str


class _RegistryCacheProbe:
    """Hold overlapping activity leases against one cache instance."""

    def __init__(
        self,
        *,
        cache: RegistryArtifactCache,
        artifact_uri: str,
        expected_holders: int,
    ) -> None:
        self.cache = cache
        self.artifact_uri = artifact_uri
        self.expected_holders = expected_holders
        self.all_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active_holders = 0
        self.peak_holders = 0

    @activity.defn(name=_PROBE_ACTIVITY_NAME)
    async def run(self, index: int) -> _ProbeResult:
        """Lease the shared artifact until every scheduled activity overlaps."""
        del index
        async with self.cache.lease([self.artifact_uri]) as registry_paths:
            self.active_holders += 1
            self.peak_holders = max(self.peak_holders, self.active_holders)
            if self.active_holders == self.expected_holders:
                self.all_entered.set()
            try:
                await self.release.wait()
                return _ProbeResult(
                    cache_instance_id=id(self.cache),
                    event_loop_id=id(asyncio.get_running_loop()),
                    thread_id=threading.get_ident(),
                    registry_path=str(registry_paths[0]),
                )
            finally:
                self.active_holders -= 1


@workflow.defn
class _RegistryCacheConcurrencyWorkflow:
    """Fan out enough activities to force overlapping cache leases."""

    @workflow.run
    async def run(self, activity_count: int) -> list[_ProbeResult]:
        """Run the cache probe activities concurrently without retries."""
        handles = [
            workflow.start_activity(
                _PROBE_ACTIVITY_NAME,
                index,
                result_type=_ProbeResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            for index in range(activity_count)
        ]
        return await asyncio.gather(*handles)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.temporal
async def test_one_temporal_worker_uses_one_cache_loop_and_thread(
    temporal_env: WorkflowEnvironment,
    tmp_path: Path,
) -> None:
    """Protect the production async-activity ownership contract.

    A real Temporal worker must schedule overlapping cache users on one event
    loop and thread while sharing one process-wide cache instance. The explicit
    production-activity assertion also prevents action execution from quietly
    moving into Temporal's synchronous thread pool, the historical failure mode
    for process-wide async storage state.
    """
    assert inspect.iscoroutinefunction(ExecutorActivities.execute_action_activity)

    activity_count = 32
    artifact_uri = "s3://bucket/temporal-shared.tar.gz"
    cache_key = compute_registry_artifact_cache_key(artifact_uri)
    cache_dir = tmp_path / "registry-cache"
    cache_dir.mkdir()
    cache = RegistryArtifactCache(cache_dir)
    registry_path = cache._paths_for(cache_key).tarball_target_dir
    registry_path.mkdir(parents=True)
    (registry_path / "module.py").write_text("VALUE = 1")

    probe = _RegistryCacheProbe(
        cache=cache,
        artifact_uri=artifact_uri,
        expected_holders=activity_count,
    )
    task_queue = f"registry-cache-concurrency-{uuid.uuid4()}"

    async with Worker(
        client=temporal_env.client,
        task_queue=task_queue,
        activities=[probe.run],
        workflows=[_RegistryCacheConcurrencyWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_concurrent_activities=activity_count,
    ):
        handle = await temporal_env.client.start_workflow(
            _RegistryCacheConcurrencyWorkflow.run,
            activity_count,
            id=f"registry-cache-concurrency-{uuid.uuid4()}",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=45),
        )
        results: list[_ProbeResult] = []
        try:
            await asyncio.wait_for(probe.all_entered.wait(), timeout=20)
            assert cache._refcount(cache_key) == activity_count
            probe.release.set()
            results = await handle.result()
        except BaseException:
            probe.release.set()
            await handle.terminate(reason="Registry cache concurrency test failed")
            raise
        finally:
            probe.release.set()

    assert probe.peak_holders == activity_count
    assert probe.active_holders == 0
    assert {result.cache_instance_id for result in results} == {id(cache)}
    assert len({result.event_loop_id for result in results}) == 1
    assert len({result.thread_id for result in results}) == 1
    assert {result.registry_path for result in results} == {str(registry_path)}
    assert cache._refcount(cache_key) == 0
    assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())
    assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())
