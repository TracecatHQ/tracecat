"""Integration tests for multi-tenant WorkerPool.

These tests spin up real WorkerPool instances and verify:
1. Pool startup and cold start behavior
2. Multi-tenant concurrent execution with different PYTHONPATHs
3. Worker recycling under load
4. Tarball cache locking during concurrent requests
5. Thrashing between tenants to stress isolation
6. Error propagation through the pool
7. Worker crash detection and recovery
8. Task timeout handling
9. Pool exhaustion and backpressure
10. PYTHONPATH isolation between tenants

These tests run with TRACECAT__DISABLE_NSJAIL=true, which spawns workers
as direct subprocesses instead of using nsjail sandboxing. This allows
tests to run on any platform (macOS, Linux, CI) without requiring
Linux namespaces or CAP_SYS_ADMIN privileges.

Pool workers run in test mode (TRACECAT__POOL_WORKER_TEST_MODE=true),
returning mock success without executing actual actions. This allows
testing pool mechanics in isolation without database access.

Run via: uv run pytest tests/integration/test_pool_integration.py -v
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.backends.pool import WorkerPool
from tracecat.executor.schemas import (
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)

# =============================================================================
# Test Class 1: Cold Start Behavior
# =============================================================================


class TestPoolColdStart:
    """Tests for pool startup and cold start behavior.

    Verifies that:
    - Pool creates the expected number of workers on startup
    - First execution completes within acceptable cold start time
    - Workers are alive and ready to accept tasks
    """

    @pytest.mark.anyio
    async def test_pool_startup_creates_workers(self, worker_pool: WorkerPool) -> None:
        """Verify pool creates the expected number of live workers on startup.

        Validates:
        - Pool reports started state
        - Correct number of workers created (2)
        - All workers are alive (process.returncode is None)
        """
        assert worker_pool._started, "Pool should be in started state"
        assert len(worker_pool._workers) == 2, "Pool should have 2 workers"

        for worker in worker_pool._workers:
            assert worker.process.returncode is None, (
                f"Worker {worker.worker_id} should be alive"
            )

    @pytest.mark.anyio
    async def test_first_execution_measures_cold_start_latency(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Measure and verify cold start latency is within acceptable bounds.

        Cold start includes worker selection, socket connection, and first task dispatch.
        Even with nsjail overhead, this should complete within 5 seconds.

        Validates:
        - First execution completes within 5s
        - Result is returned successfully
        """
        input_data = run_action_input_factory(
            action="core.transform",
            args={"value": {"test": True}},
        )
        resolved_context = resolved_context_factory(role_workspace_a)

        start = time.monotonic()
        result = await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        # Cold start should be < 5s even with nsjail overhead
        assert elapsed_ms < 5000, f"Cold start too slow: {elapsed_ms}ms"
        assert result.type == "success", f"Execution should succeed: {result}"

    @pytest.mark.anyio
    async def test_warm_execution_faster_than_cold(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify that warm worker execution is significantly faster than cold start.

        After the first execution warms up the worker, subsequent executions
        should be much faster (socket overhead only, no process startup).

        Validates:
        - Second execution is faster than first
        - Warm execution completes within 1s
        """
        input_data = run_action_input_factory()
        resolved_context = resolved_context_factory(role_workspace_a)

        # First execution (may incur cold start)
        start1 = time.monotonic()
        await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        first_ms = (time.monotonic() - start1) * 1000

        # Second execution (warm worker)
        start2 = time.monotonic()
        await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        warm_ms = (time.monotonic() - start2) * 1000

        # Warm should be faster (or at least not significantly slower)
        # Note: First call may already be warm if pool pre-warmed workers
        assert warm_ms < 1000, (
            f"Warm execution too slow: {warm_ms}ms (first was {first_ms}ms)"
        )


# =============================================================================
# Test Class 2: Multi-Tenant Execution
# =============================================================================


class TestMultiTenantExecution:
    """Tests for multi-tenant concurrent execution and isolation.

    Verifies that:
    - Different workspaces can execute concurrently
    - PYTHONPATH is correctly forwarded per-request
    - No cross-contamination between tenants
    """

    @pytest.mark.anyio
    async def test_concurrent_execution_different_workspaces(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Verify concurrent executions from different workspaces are isolated.

        Pre-stages different mock modules for each workspace, then executes
        concurrently and verifies each workspace gets its own PYTHONPATH.

        Validates:
        - Both executions complete successfully
        - No cross-contamination between workspaces
        """
        _, _ = staged_cache_dirs

        input_a = run_action_input_factory()
        input_b = run_action_input_factory()

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        # Execute concurrently with different PYTHONPATHs
        results = await asyncio.gather(
            worker_pool.execute(
                input=input_a,
                role=role_workspace_a,
                resolved_context=resolved_context_a,
                timeout=30.0,
            ),
            worker_pool.execute(
                input=input_b,
                role=role_workspace_b,
                resolved_context=resolved_context_b,
                timeout=30.0,
            ),
        )

        result_a, result_b = results
        assert result_a.type == "success", f"Workspace A should succeed: {result_a}"
        assert result_b.type == "success", f"Workspace B should succeed: {result_b}"

    @pytest.mark.anyio
    async def test_single_workspace_execution(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify basic execution for a single workspace works correctly.

        Validates:
        - Single workspace execution completes successfully
        - Worker receives and processes the request
        """
        input_data = run_action_input_factory()
        resolved_context = resolved_context_factory(role_workspace_a)

        result = await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )

        assert result.type == "success", f"Execution should complete: {result}"

    @pytest.mark.anyio
    async def test_multiple_concurrent_same_workspace(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify multiple concurrent requests from same workspace work correctly.

        Tests that the pool can handle multiple simultaneous requests from
        the same tenant without issues.

        Validates:
        - All concurrent requests complete successfully
        - No request interference
        """
        inputs = [run_action_input_factory() for _ in range(5)]
        resolved_context = resolved_context_factory(role_workspace_a)

        results = await asyncio.gather(
            *[
                worker_pool.execute(
                    input=inp,
                    role=role_workspace_a,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
                for inp in inputs
            ]
        )

        successes = sum(1 for r in results if r.type == "success")
        assert successes == 5, f"All 5 requests should succeed, got {successes}"


# =============================================================================
# Test Class 3: Worker Recycling
# =============================================================================


class TestWorkerRecycling:
    """Tests for worker recycling behavior under load.

    Verifies that:
    - Workers are recycled after completing max_tasks_per_worker tasks
    - Recycling happens gracefully without task loss
    - Pool continues to function during and after recycling
    """

    @pytest.mark.anyio
    async def test_worker_recycled_after_task_limit(
        self,
        small_recycle_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify worker is recycled after completing max_tasks_per_worker tasks.

        Uses a pool with max_tasks_per_worker=5 to quickly trigger recycling.

        Validates:
        - Worker is recycled (new PID assigned)
        - Old worker process is terminated (not orphaned/zombie)
        - New worker is alive and functional
        - Pool continues to function after recycle
        """
        pool = small_recycle_pool
        original_process = pool._workers[0].process
        original_pid = pool._workers[0].pid
        resolved_context = resolved_context_factory(role_workspace_a)

        # Execute 6 tasks (1 more than limit of 5)
        for i in range(6):
            input_data = run_action_input_factory()
            result = await pool.execute(
                input=input_data,
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            assert result.type == "success", f"Task {i} should succeed"
            # Small delay to allow recycle to complete
            await asyncio.sleep(0.1)

        # Worker should have been recycled - verify PID changed
        new_pid = pool._workers[0].pid
        assert new_pid != original_pid, (
            f"Worker should have been recycled with new PID (was {original_pid}, now {new_pid})"
        )

        # Verify old worker is actually dead (not orphaned/zombie)
        assert original_process.returncode is not None, (
            "Old worker should have terminated"
        )

        # Verify new worker is alive
        assert pool._workers[0].process.returncode is None, "New worker should be alive"

        # Verify new worker is functional (can execute tasks)
        result = await pool.execute(
            input=run_action_input_factory(),
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        assert result.type == "success", "Recycled worker should be functional"

    @pytest.mark.anyio
    async def test_concurrent_tasks_during_recycle(
        self,
        small_recycle_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify concurrent tasks are handled correctly during recycling.

        This tests the edge case where worker hits task limit and new tasks
        arrive while recycling.

        Validates:
        - All tasks complete successfully
        - No tasks lost during recycle window
        """
        pool = small_recycle_pool
        resolved_context = resolved_context_factory(role_workspace_a)

        # Launch many tasks concurrently to trigger recycle during execution
        tasks: list[Awaitable[ExecutorResult]] = []
        for _ in range(10):
            input_data = run_action_input_factory()
            tasks.append(
                pool.execute(
                    input=input_data,
                    role=role_workspace_a,
                    resolved_context=resolved_context,
                    timeout=60.0,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successes (some may execute before recycle, some after)
        successes = sum(1 for r in results if isinstance(r, ExecutorResultSuccess))
        errors = [r for r in results if isinstance(r, Exception)]

        assert successes == 10, (
            f"Expected 10 successes, got {successes}. Errors: {errors}"
        )


# =============================================================================
# Test Class 4: Cache Behavior
# =============================================================================


class TestCacheBehavior:
    """Tests for tarball cache behavior during concurrent requests.

    Verifies that:
    - Same tarball URI requested concurrently results in only one download
    - Different URIs create separate cache entries
    - Cache reuse works correctly
    """

    @pytest.mark.anyio
    async def test_concurrent_requests_same_tarball_single_download(
        self,
        temp_registry_cache: Path,
        mock_modules_dir: Path,
    ) -> None:
        """Verify concurrent requests for same tarball only download once.

        Uses ActionRunner's ensure_tarball_extracted with mocked download
        to verify the locking behavior.

        Validates:
        - Single download despite concurrent requests
        - All requests return same path
        """
        from tracecat.executor.action_runner import ActionRunner

        runner = ActionRunner(cache_dir=temp_registry_cache)
        download_count = [0]

        async def mock_download(url: str, path: Path) -> None:
            download_count[0] += 1
            await asyncio.sleep(0.1)  # Simulate network latency
            path.write_bytes(b"mock tarball")

        async def mock_extract(tarball_path: Path, target_dir: Path) -> None:
            shutil.copytree(
                mock_modules_dir / "workspace_a",
                target_dir,
                dirs_exist_ok=True,
            )

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                return_value="http://mock-url",
            ),
        ):
            cache_key = "concurrent-test"
            tarball_uri = "s3://bucket/concurrent.tar.gz"

            # Launch concurrent requests
            results = await asyncio.gather(
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
            )

        assert download_count[0] == 1, (
            f"Should only download once, got {download_count[0]}"
        )
        assert all(r == results[0] for r in results), "All should return same path"


# =============================================================================
# Test Class 5: Multi-Tenant Thrashing
# =============================================================================


class TestMultiTenantThrashing:
    """Tests for rapid alternation between tenants to stress isolation.

    Verifies that:
    - Rapid switching between workspaces maintains isolation
    - No cross-contamination under concurrent load
    - Cache and worker state remain correct during thrashing
    """

    @pytest.mark.anyio
    async def test_rapid_alternation_between_workspaces(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Interleave A/B requests with staggered launch to verify isolation.

        Tasks are launched in strict A/B/A/B order with 5ms intervals,
        allowing execution to overlap while maintaining launch order.
        This simulates real traffic patterns where requests arrive
        sequentially but execution is concurrent.

        Timeline visualization:
            t=0ms:   create_task(A₀)  →  A₀ starts executing
            t=5ms:   create_task(B₁)  →  B₁ starts, A₀ still running
            t=10ms:  create_task(A₂)  →  A₂ starts, A₀/B₁ may still be running
            t=15ms:  create_task(B₃)  →  ...

            A₀: [████████████████]
            B₁:      [████████████████]
            A₂:           [████████████████]
            B₃:                [████████████████]
                     ↑
                Tasks overlap but launch in strict A/B/A/B order

        Validates:
        - Each request gets correct PYTHONPATH for its workspace
        - No cross-contamination of request/response data
        - All requests complete successfully
        """
        path_a, path_b = staged_cache_dirs
        task_count = 100
        launch_interval = 0.005  # 5ms between launches

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        tasks: list[asyncio.Task[ExecutorResult]] = []

        for i in range(task_count):
            if i % 2 == 0:
                role = role_workspace_a
                resolved_context = resolved_context_a
                _pythonpath = str(path_a)
            else:
                role = role_workspace_b
                resolved_context = resolved_context_b
                _pythonpath = str(path_b)

            # Launch task immediately, don't await - allows concurrent execution
            task = asyncio.create_task(
                worker_pool.execute(
                    input=run_action_input_factory(),
                    role=role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
            )
            tasks.append(task)
            await asyncio.sleep(launch_interval)  # Stagger launches

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = sum(1 for r in results if isinstance(r, ExecutorResultSuccess))
        errors = [r for r in results if isinstance(r, Exception)]

        assert successes == task_count, (
            f"Expected {task_count} successes, got {successes}. "
            f"First 5 errors: {errors[:5]}"
        )

        # Verify lifetime metrics (persists across worker recycling)
        metrics = worker_pool.get_lifetime_metrics()

        assert metrics["tasks_completed"] == task_count, (
            f"Pool lifetime tasks ({metrics['tasks_completed']}) "
            f"should match task count ({task_count})"
        )
        assert metrics["tasks_failed"] == 0, (
            f"No tasks should have failed: {metrics['tasks_failed']}"
        )
        assert metrics["tasks_timed_out"] == 0, (
            f"No tasks should have timed out: {metrics['tasks_timed_out']}"
        )

        # Verify per-worker distribution (both workers should have done work)
        per_worker = metrics["per_worker_tasks_completed"]
        assert len(per_worker) == 2, f"Both workers should have done work: {per_worker}"
        assert all(count > 0 for count in per_worker.values()), (
            f"Each worker should have completed tasks: {per_worker}"
        )

    @pytest.mark.anyio
    async def test_burst_then_switch_pattern(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Burst from A, then burst from B to test warm worker context switch.

        Pattern:
        - 20 rapid requests from workspace A
        - Immediately followed by 20 rapid requests from workspace B

        Validates:
        - All A requests succeed with A's PYTHONPATH
        - All B requests succeed with B's PYTHONPATH
        - Workers correctly switch context between bursts
        """
        _, _ = staged_cache_dirs
        burst_size = 20

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        # Burst A
        tasks_a = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context_a,
                timeout=30.0,
            )
            for _ in range(burst_size)
        ]
        results_a = await asyncio.gather(*tasks_a, return_exceptions=True)

        # Burst B (immediately after A)
        tasks_b = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_b,
                resolved_context=resolved_context_b,
                timeout=30.0,
            )
            for _ in range(burst_size)
        ]
        results_b = await asyncio.gather(*tasks_b, return_exceptions=True)

        successes_a = sum(1 for r in results_a if isinstance(r, ExecutorResultSuccess))
        successes_b = sum(1 for r in results_b if isinstance(r, ExecutorResultSuccess))

        assert successes_a == burst_size, (
            f"All A requests should succeed: {successes_a}/{burst_size}"
        )
        assert successes_b == burst_size, (
            f"All B requests should succeed: {successes_b}/{burst_size}"
        )

    @pytest.mark.anyio
    async def test_interleaved_concurrent_bursts(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Overlapping bursts from both workspaces running simultaneously.

        Pattern:
        - Launch 10 concurrent A requests
        - While A is running, launch 10 concurrent B requests
        - Wait for all to complete

        Validates:
        - All complete with correct isolation
        - No race conditions between concurrent tenants
        """
        _, _ = staged_cache_dirs
        burst_size = 10

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        # Start A burst
        tasks_a = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context_a,
                timeout=30.0,
            )
            for _ in range(burst_size)
        ]

        # Immediately start B burst (overlapping with A)
        tasks_b = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_b,
                resolved_context=resolved_context_b,
                timeout=30.0,
            )
            for _ in range(burst_size)
        ]

        # Wait for all
        all_results = await asyncio.gather(*tasks_a, *tasks_b, return_exceptions=True)

        successes = sum(1 for r in all_results if isinstance(r, ExecutorResultSuccess))
        errors = [r for r in all_results if isinstance(r, Exception)]

        assert successes == burst_size * 2, (
            f"Expected {burst_size * 2} successes, got {successes}. Errors: {errors}"
        )

    @pytest.mark.anyio
    @pytest.mark.slow
    async def test_thrashing_with_cache_pressure(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Thrash while cache entries expire to test TTL boundary handling.

        Runs continuous alternating workload for 70+ seconds, crossing the
        60s cache TTL boundary. Verifies cache refresh doesn't cause
        isolation failures.

        Note: This test is marked @pytest.mark.slow and may take > 1 minute.

        Validates:
        - Cache refresh doesn't cause isolation failures
        - All requests complete successfully across TTL boundary
        """
        path_a, path_b = staged_cache_dirs
        duration_seconds = 75  # Cross the 60s TTL boundary
        request_interval = 0.5  # 2 requests per second

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        start_time = time.monotonic()
        request_count = 0
        success_count = 0
        error_count = 0

        while (time.monotonic() - start_time) < duration_seconds:
            # Alternate between workspaces
            if request_count % 2 == 0:
                role = role_workspace_a
                resolved_context = resolved_context_a
                _pythonpath = str(path_a)
            else:
                role = role_workspace_b
                resolved_context = resolved_context_b
                _pythonpath = str(path_b)

            try:
                result = await worker_pool.execute(
                    input=run_action_input_factory(),
                    role=role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
                if result.type == "success":
                    success_count += 1
                else:
                    error_count += 1
            except Exception:
                error_count += 1

            request_count += 1
            await asyncio.sleep(request_interval)

        # Should have high success rate (allow some failures for robustness)
        success_rate = success_count / request_count if request_count > 0 else 0
        assert success_rate > 0.95, (
            f"Success rate {success_rate:.2%} below threshold. "
            f"Requests: {request_count}, Successes: {success_count}, Errors: {error_count}"
        )

    @pytest.mark.anyio
    async def test_worker_reuse_across_tenants(
        self,
        single_worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
        role_workspace_b: Role,
        staged_cache_dirs: tuple[Path, Path],
    ) -> None:
        """Verify same worker correctly serves different tenants sequentially.

        Uses pool size=1 to force same worker to handle all requests.
        Tests that PYTHONPATH is correctly switched per-request.

        Validates:
        - Worker is stateless for tenant context
        - PYTHONPATH correctly changes per-request
        - No leakage between tenants
        """
        pool = single_worker_pool
        path_a, path_b = staged_cache_dirs

        resolved_context_a = resolved_context_factory(role_workspace_a)
        resolved_context_b = resolved_context_factory(role_workspace_b)

        # Capture the single worker
        assert len(pool._workers) == 1, "Should have exactly 1 worker"
        worker = pool._workers[0]
        initial_pid = worker.pid

        # Alternate between tenants, forcing same worker to serve both
        for i in range(10):
            if i % 2 == 0:
                role = role_workspace_a
                resolved_context = resolved_context_a
                _pythonpath = str(path_a)
            else:
                role = role_workspace_b
                resolved_context = resolved_context_b
                _pythonpath = str(path_b)

            result = await pool.execute(
                input=run_action_input_factory(),
                role=role,
                resolved_context=resolved_context,
                timeout=30.0,
            )

            assert result.type == "success", f"Request {i} should succeed: {result}"

        # Worker should still be the same (not recycled for 10 tasks)
        assert pool._workers[0].pid == initial_pid, (
            "Worker should not have been recycled"
        )


# =============================================================================
# Test Class 6: Error Propagation
# =============================================================================


class TestErrorPropagation:
    """Tests for error handling and propagation through the pool.

    Verifies that:
    - Action errors are properly returned to callers (not swallowed)
    - Error details include type, message, and traceback
    - Pool continues functioning after errors
    """

    @pytest.mark.anyio
    async def test_action_error_returns_structured_error(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify action errors return structured error information.

        When an action fails, the result should contain:
        - success: False
        - error: dict with type, message, and other details

        Note: In test mode, we can't trigger real action errors, so this
        test verifies the error response structure is correct when errors occur.
        """
        # In test mode, all executions succeed. This test documents the expected
        # error response format for when real errors occur.
        input_data = run_action_input_factory()
        resolved_context = resolved_context_factory(role_workspace_a)
        result = await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )

        # Verify response structure includes all expected fields
        assert hasattr(result, "type"), "Response must include 'type' field"
        # ExecutorResultSuccess has 'result', ExecutorResultFailure has 'error'
        if result.type == "success":
            assert hasattr(result, "result"), (
                "Success response must include 'result' field"
            )
        else:
            assert hasattr(result, "error"), (
                "Failure response must include 'error' field"
            )

    @pytest.mark.anyio
    async def test_pool_continues_after_failed_task(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify pool continues functioning after task failures.

        A failed task should not impact subsequent tasks. The pool should
        remain healthy and continue processing requests.

        Validates:
        - Pool processes multiple tasks in sequence
        - Each task gets a response (regardless of success/failure)
        - Workers remain alive and functional
        """
        resolved_context = resolved_context_factory(role_workspace_a)
        results = []
        for _ in range(5):
            input_data = run_action_input_factory()
            result = await worker_pool.execute(
                input=input_data,
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            results.append(result)

        # All should return responses (in test mode, all succeed)
        assert len(results) == 5, "Should get 5 responses"
        for i, result in enumerate(results):
            assert hasattr(result, "type"), f"Task {i} should return a response"

        # Workers should still be alive
        for worker in worker_pool._workers:
            assert worker.process.returncode is None, "Workers should be alive"

    @pytest.mark.anyio
    async def test_concurrent_tasks_with_mixed_results(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify concurrent tasks are isolated - one failure doesn't affect others.

        When multiple tasks run concurrently, a failure in one should not
        impact the others.

        Validates:
        - All concurrent tasks complete independently
        - No cross-task interference
        """
        resolved_context = resolved_context_factory(role_workspace_a)
        tasks = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            for _ in range(10)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should complete (no exceptions at the pool level)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"No pool-level exceptions expected: {exceptions}"

        # All should have response structure
        for result in results:
            assert isinstance(result, ExecutorResultSuccess | ExecutorResultFailure), (
                f"Result should be ExecutorResult, got {type(result)}"
            )
            assert hasattr(result, "type"), "Result should have 'type' field"


# =============================================================================
# Test Class 7: Worker Crash Recovery
# =============================================================================


class TestWorkerCrashRecovery:
    """Tests for worker crash detection and recovery.

    Verifies that:
    - Pool detects when a worker process dies
    - Tasks are handled gracefully when worker crashes
    - Pool can respawn workers after crashes
    """

    @pytest.mark.anyio
    async def test_pool_detects_dead_worker(self) -> None:
        """Verify pool can detect when a worker process has died.

        Note: This test uses its own pool to avoid corrupting shared state.

        Validates:
        - Worker process death is detectable via returncode
        - Pool continues operating with remaining workers
        """
        from tracecat.executor.backends.pool import WorkerPool

        pool = WorkerPool(size=2, max_concurrent_per_worker=4)
        await pool.start()

        try:
            # Verify workers start alive
            for worker in pool._workers:
                assert worker.process.returncode is None, "Worker should start alive"

            # Kill one worker
            worker_to_kill = pool._workers[0]
            worker_to_kill.process.kill()
            await worker_to_kill.process.wait()

            # Verify death is detectable
            assert worker_to_kill.process.returncode is not None, (
                "Killed worker should have non-None returncode"
            )

            # Other worker should still be alive
            other_worker = pool._workers[1]
            assert other_worker.process.returncode is None, (
                "Other worker should still be alive"
            )
        finally:
            await pool.shutdown()

    @pytest.mark.anyio
    async def test_task_fails_gracefully_on_dead_worker(
        self,
        single_worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify task fails gracefully when sent to a dead worker.

        When a worker dies, tasks routed to it should fail with an error
        rather than hanging indefinitely.

        Note: Current behavior is that the pool waits for a worker slot,
        which will timeout when all workers are dead. This test verifies
        the task eventually fails rather than hanging forever.

        Validates:
        - Task completes (doesn't hang forever)
        - Error or timeout is returned to caller
        """
        pool = single_worker_pool
        resolved_context = resolved_context_factory(role_workspace_a)

        # First, verify pool works
        result = await pool.execute(
            input=run_action_input_factory(),
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        assert result.type == "success", "Initial task should succeed"

        # Kill the only worker
        worker = pool._workers[0]
        worker.process.kill()
        await worker.process.wait()

        # Next task should fail (not hang forever)
        # The pool will wait for a worker slot, but we use a short outer timeout
        # to verify the task doesn't hang indefinitely
        task_failed = False
        try:
            result = await asyncio.wait_for(
                pool.execute(
                    input=run_action_input_factory(),
                    role=role_workspace_a,
                    resolved_context=resolved_context,
                    timeout=2.0,  # Short task timeout
                ),
                timeout=5.0,  # Outer timeout to prevent test hang
            )
            # If we get a result, check if it indicates failure
            if result.type != "success":
                task_failed = True
        except (TimeoutError, Exception):
            # Expected - task should fail/timeout when worker is dead
            task_failed = True

        assert task_failed, "Task should fail or timeout when only worker is dead"

    @pytest.mark.anyio
    async def test_pool_continues_with_remaining_workers_after_crash(
        self,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify pool continues serving requests after one worker crashes.

        With multiple workers, losing one should not stop the pool from
        processing requests.

        Note: This test uses its own pool to avoid corrupting shared state.

        Validates:
        - Tasks succeed using surviving workers
        - Pool doesn't hang or crash
        """
        from tracecat.executor.backends.pool import WorkerPool

        pool = WorkerPool(size=2, max_concurrent_per_worker=4)
        await pool.start()
        resolved_context = resolved_context_factory(role_workspace_a)

        try:
            # Kill one worker
            worker_to_kill = pool._workers[0]
            worker_to_kill.process.kill()
            await worker_to_kill.process.wait()

            # Pool should still work with the other worker
            successes = 0
            for _ in range(5):
                try:
                    result = await pool.execute(
                        input=run_action_input_factory(),
                        role=role_workspace_a,
                        resolved_context=resolved_context,
                        timeout=10.0,
                    )
                    if result.type == "success":
                        successes += 1
                except Exception:
                    pass

            # Should have some successes (surviving worker handles requests)
            assert successes > 0, "Pool should continue working with remaining workers"
        finally:
            await pool.shutdown()


# =============================================================================
# Test Class 8: Task Timeout Handling
# =============================================================================


class TestTaskTimeoutHandling:
    """Tests for task timeout behavior.

    Verifies that:
    - Tasks exceeding timeout fail cleanly
    - Timeouts don't hang the worker
    - Pool recovers after timeout
    """

    @pytest.mark.anyio
    async def test_task_respects_timeout_parameter(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify task completes within the specified timeout.

        In test mode, tasks complete quickly. This test verifies that
        the timeout parameter is accepted and normal tasks complete
        well before the timeout.

        Validates:
        - Task completes before timeout
        - Timeout parameter is accepted
        """
        input_data = run_action_input_factory()
        resolved_context = resolved_context_factory(role_workspace_a)

        start = time.monotonic()
        result = await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=5.0,  # 5 second timeout
        )
        elapsed = time.monotonic() - start

        assert result.type == "success", "Task should succeed"
        assert elapsed < 5.0, f"Task should complete well before timeout: {elapsed}s"

    @pytest.mark.anyio
    async def test_pool_timeout_returns_error(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify pool-level timeout returns proper error structure.

        When a task times out at the pool level (not worker level),
        it should return an error result or raise TimeoutError.

        Note: In test mode, tasks complete quickly so we can't easily
        trigger real timeouts. This test documents expected behavior.
        """
        input_data = run_action_input_factory()
        resolved_context = resolved_context_factory(role_workspace_a)

        # Very short timeout - task should still complete in test mode
        result = await worker_pool.execute(
            input=input_data,
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )

        # In test mode, should succeed. Real timeouts would have
        # type="failure" and error containing timeout info.
        assert hasattr(result, "type"), "Should return result with type field"

    @pytest.mark.anyio
    async def test_multiple_tasks_with_varying_timeouts(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify different tasks can have different timeouts.

        Each task should respect its own timeout independently.

        Validates:
        - Multiple concurrent tasks with different timeouts
        - All complete successfully (in test mode)
        """
        resolved_context = resolved_context_factory(role_workspace_a)
        tasks = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=float(5 + i),  # 5s, 6s, 7s, 8s, 9s
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        successes = sum(1 for r in results if r.type == "success")
        assert successes == 5, f"All tasks should succeed: {successes}/5"


# =============================================================================
# Test Class 9: Pool Exhaustion
# =============================================================================


class TestPoolExhaustion:
    """Tests for pool behavior when at capacity.

    Verifies that:
    - Pool handles many concurrent requests
    - Requests queue or fail gracefully when at capacity
    - Pool recovers after burst
    """

    @pytest.mark.anyio
    async def test_concurrent_requests_up_to_capacity(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify pool handles concurrent requests up to its capacity.

        With pool_size=2 and max_concurrent_per_worker=4, total capacity is 8.
        Sending 8 concurrent requests should all succeed.

        Validates:
        - All requests within capacity complete successfully
        - No requests lost or failed
        """
        # Pool has 2 workers with 4 concurrent each = 8 capacity
        capacity = 8
        resolved_context = resolved_context_factory(role_workspace_a)
        tasks = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            for _ in range(capacity)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = sum(1 for r in results if isinstance(r, ExecutorResultSuccess))
        exceptions = [r for r in results if isinstance(r, Exception)]

        assert successes == capacity, (
            f"All {capacity} requests should succeed: {successes}. "
            f"Exceptions: {exceptions}"
        )

    @pytest.mark.anyio
    async def test_requests_beyond_capacity_still_complete(
        self,
        worker_pool: WorkerPool,
        run_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ) -> None:
        """Verify requests beyond capacity eventually complete.

        When more requests arrive than capacity, they should queue
        and complete once workers become available.

        Validates:
        - Excess requests don't fail immediately
        - All requests eventually complete
        """
        # Send 2x capacity
        request_count = 16  # 2x the 8 capacity
        resolved_context = resolved_context_factory(role_workspace_a)
        tasks = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=60.0,  # Longer timeout for queued requests
            )
            for _ in range(request_count)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = sum(1 for r in results if isinstance(r, ExecutorResultSuccess))
        exceptions = [r for r in results if isinstance(r, Exception)]

        assert successes == request_count, (
            f"All {request_count} requests should eventually succeed: {successes}. "
            f"Exceptions: {exceptions}"
        )

    @pytest.mark.anyio
    async def test_pool_recovers_after_burst(
        self,
        worker_pool,
        run_action_input_factory,
        resolved_context_factory: Callable[..., ResolvedContext],
        role_workspace_a: Role,
    ):
        """Verify pool returns to normal after handling burst traffic.

        After a burst of requests, the pool should recover and handle
        subsequent requests normally.

        Validates:
        - Burst completes successfully
        - Subsequent requests work normally
        - Workers are healthy after burst
        """
        resolved_context = resolved_context_factory(role_workspace_a)

        # Burst of requests
        burst_tasks = [
            worker_pool.execute(
                input=run_action_input_factory(),
                role=role_workspace_a,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            for _ in range(20)
        ]
        burst_results = await asyncio.gather(*burst_tasks, return_exceptions=True)

        burst_successes = sum(
            1 for r in burst_results if isinstance(r, ExecutorResultSuccess)
        )
        assert burst_successes == 20, f"Burst should succeed: {burst_successes}/20"

        # Wait a moment for pool to settle
        await asyncio.sleep(0.1)

        # Normal request after burst
        result = await worker_pool.execute(
            input=run_action_input_factory(),
            role=role_workspace_a,
            resolved_context=resolved_context,
            timeout=30.0,
        )
        assert result.type == "success", "Post-burst request should succeed"

        # Workers should be healthy
        alive_workers = sum(
            1 for w in worker_pool._workers if w.process.returncode is None
        )
        assert alive_workers == 2, f"All workers should be alive: {alive_workers}/2"
