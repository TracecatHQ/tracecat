"""Performance benchmarks for executor backends.

pytest-benchmark based performance tests measuring:
- Per-action latency by backend
- Cold start latency (backend initialization overhead)
- Concurrent throughput (actions/second under concurrency)
- Memory usage after burst workloads

IMPORTANT: These benchmarks require the full development stack to be running:
    - Docker services via `just dev` (PostgreSQL, Redis, MinIO, Temporal)
    - Registry must be accessible for action execution

Run benchmarks:
    # Start development stack first
    just dev

    # All benchmarks with test backend (default)
    uv run pytest tests/backends/test_backend_benchmarks.py -v

    # Benchmarks only (skip non-benchmark tests)
    uv run pytest tests/backends/test_backend_benchmarks.py --benchmark-only -v

    # Save results to JSON
    uv run pytest tests/backends/test_backend_benchmarks.py \
        --benchmark-only --benchmark-json=results.json -v

    # Compare results
    pytest-benchmark compare results.json --columns=min,max,mean,stddev

    # With specific backend (requires nsjail on Linux)
    TRACECAT__EXECUTOR_BACKEND=pool \
        uv run pytest tests/backends/test_backend_benchmarks.py -v

Note: Sandboxed backends (pool, ephemeral) require Linux with nsjail.
"""

from __future__ import annotations

import asyncio
import gc
import resource
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.backends.base import ExecutorBackend
    from tracecat.executor.schemas import ResolvedContext

# Mark all tests in this module as integration tests requiring infrastructure
pytestmark = pytest.mark.integration


# =============================================================================
# Helper Functions
# =============================================================================


def get_memory_usage_mb() -> float:
    """Get current memory usage in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # maxrss is in kilobytes on Linux, bytes on macOS
    import platform

    if platform.system() == "Darwin":
        return usage.ru_maxrss / (1024 * 1024)
    return usage.ru_maxrss / 1024


async def run_async_benchmark(
    coro_factory: Callable[[], Awaitable[Any]],
    rounds: int = 10,
    warmup_rounds: int = 2,
) -> list[float]:
    """Run an async coroutine multiple times and collect timing data.

    Args:
        coro_factory: Factory function that returns a coroutine to benchmark
        rounds: Number of timed rounds
        warmup_rounds: Number of warmup rounds (not timed)

    Returns:
        List of elapsed times in seconds for each round
    """
    # Warmup
    for _ in range(warmup_rounds):
        await coro_factory()

    # Timed rounds
    times = []
    for _ in range(rounds):
        start = time.perf_counter()
        await coro_factory()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return times


# =============================================================================
# Simple Action Latency Benchmarks
# =============================================================================


class TestSimpleActionLatency:
    """Benchmarks for per-action execution latency.

    Measures the time to execute a simple core.transform action
    across different backends. This represents the minimum overhead
    for executing any action.

    Note: These tests measure execution latency including registry lookup.
    If the registry is not available, tests will measure failure path latency.
    """

    @pytest.mark.anyio
    async def test_test_backend_latency(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Benchmark test backend action latency.

        Test backend executes in-process with no subprocess overhead.
        This establishes the baseline latency for action execution.

        Expected: < 50ms per action (when registry is available)
        """
        input_data = simple_action_input_factory(
            action="core.transform.reshape",
            args={"value": {"benchmark": "direct"}},
        )
        resolved_context = resolved_context_factory(
            role=benchmark_role,
            action="core.transform.reshape",
            args={"value": {"benchmark": "direct"}},
        )

        # Track if we're measuring success or failure path
        success_count = 0

        async def execute_action():
            nonlocal success_count
            result = await test_backend.execute(
                input=input_data,
                role=benchmark_role,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            if result.type == "success":
                success_count += 1
            return result

        # Run manual benchmark since pytest-benchmark doesn't handle async well
        times = await run_async_benchmark(execute_action, rounds=20, warmup_rounds=3)

        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000

        # Report what we measured
        print(f"\nTest backend latency (n={len(times)}, successes={success_count}):")
        print(f"  Mean: {avg_ms:.2f}ms")
        print(f"  Min:  {min_ms:.2f}ms")
        print(f"  Max:  {max_ms:.2f}ms")

        if success_count == 0:
            print("  Note: Measured failure path (registry not available)")

        # Verify reasonable latency
        assert avg_ms < 100, f"Average latency {avg_ms:.2f}ms exceeds 100ms threshold"

    @pytest.mark.anyio
    async def test_direct_backend_latency(
        self,
        direct_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Benchmark subprocess direct backend action latency."""
        input_data = simple_action_input_factory(
            action="core.transform.reshape",
            args={"value": {"benchmark": "direct-subprocess"}},
        )
        resolved_context = resolved_context_factory(
            role=benchmark_role,
            action="core.transform.reshape",
            args={"value": {"benchmark": "direct-subprocess"}},
        )

        success_count = 0

        async def execute_action():
            nonlocal success_count
            try:
                result = await direct_backend.execute(
                    input=input_data,
                    role=benchmark_role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
            except Exception:
                # If tarballs are unavailable, direct backend can fail while
                # preparing subprocess environment. We still want latency data.
                return None
            if result.type == "success":
                success_count += 1
            return result

        times = await run_async_benchmark(execute_action, rounds=10, warmup_rounds=2)

        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000

        print(f"\nDirect backend latency (n={len(times)}, successes={success_count}):")
        print(f"  Mean: {avg_ms:.2f}ms")
        print(f"  Min:  {min_ms:.2f}ms")
        print(f"  Max:  {max_ms:.2f}ms")

        if success_count == 0:
            print("  Note: Measured failure path (registry not available)")

        # Subprocess path should stay comfortably under 5s in local runs.
        assert avg_ms < 5000, f"Average latency {avg_ms:.2f}ms exceeds 5000ms threshold"

    @pytest.mark.anyio
    async def test_transform_action_latency_stats(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Collect detailed latency statistics for transform actions.

        This test measures latency across multiple runs and reports
        statistics without using the benchmark fixture (for comparison).
        """
        input_data = simple_action_input_factory()
        resolved_context = resolved_context_factory(role=benchmark_role)
        success_count = 0

        async def execute():
            nonlocal success_count
            result = await test_backend.execute(
                input=input_data,
                role=benchmark_role,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            if result.type == "success":
                success_count += 1
            return result

        times = await run_async_benchmark(execute, rounds=50, warmup_rounds=5)

        # Calculate statistics
        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000

        # Log stats for visibility
        print(f"\nLatency stats (n={len(times)}, successes={success_count}):")
        print(f"  Mean: {avg_ms:.2f}ms")
        print(f"  Min:  {min_ms:.2f}ms")
        print(f"  Max:  {max_ms:.2f}ms")

        if success_count == 0:
            print("  Note: Measured failure path (registry not available)")

        # Assert reasonable latency (< 100ms average for in-process)
        # This holds for both success and failure paths
        assert avg_ms < 100, f"Average latency {avg_ms:.2f}ms exceeds 100ms threshold"


# =============================================================================
# Cold Start Latency Benchmarks
# =============================================================================


class TestColdStartLatency:
    """Benchmarks for backend initialization overhead.

    Measures the time to initialize a backend from scratch,
    which affects first-action latency in new worker processes.
    """

    @pytest.mark.anyio
    async def test_direct_backend_cold_start(self) -> None:
        """Benchmark test backend cold start time.

        Measures time to create and start a new TestBackend instance.
        Test backend has minimal startup cost (no subprocess spawning).

        Expected: < 10ms
        """
        from tracecat.executor.backends.test import TestBackend

        async def cold_start():
            backend = TestBackend()
            await backend.start()
            await backend.shutdown()

        times = await run_async_benchmark(cold_start, rounds=10, warmup_rounds=2)

        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000

        print(f"\nTest backend cold start (n={len(times)}):")
        print(f"  Mean: {avg_ms:.2f}ms")
        print(f"  Min:  {min_ms:.2f}ms")
        print(f"  Max:  {max_ms:.2f}ms")

        # Cold start should be fast (< 50ms)
        assert avg_ms < 50, f"Cold start {avg_ms:.2f}ms exceeds 50ms threshold"

    @pytest.mark.anyio
    async def test_first_action_after_cold_start(
        self,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Measure first action latency including backend startup.

        This simulates the real-world scenario where a new worker
        starts and immediately executes an action.
        """
        from tracecat.executor.backends.test import TestBackend

        input_data = simple_action_input_factory()
        resolved_context = resolved_context_factory(role=benchmark_role)
        times = []
        success_count = 0

        for _ in range(10):
            backend = TestBackend()
            await backend.start()

            start = time.perf_counter()
            result = await backend.execute(
                input=input_data,
                role=benchmark_role,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            elapsed = time.perf_counter() - start
            times.append(elapsed)

            if result.type == "success":
                success_count += 1

            await backend.shutdown()

        avg_ms = sum(times) / len(times) * 1000
        print(
            f"\nFirst action after cold start (n={len(times)}, successes={success_count}):"
        )
        print(f"  Avg: {avg_ms:.2f}ms")

        if success_count == 0:
            print("  Note: Measured failure path (registry not available)")

        # First action should still be reasonably fast (including failure path)
        assert avg_ms < 200, f"First action latency {avg_ms:.2f}ms exceeds 200ms"


# =============================================================================
# Concurrent Throughput Benchmarks
# =============================================================================


class TestConcurrentThroughput:
    """Benchmarks for concurrent action execution throughput.

    Measures how many actions per second can be executed when
    running multiple actions concurrently.
    """

    @pytest.mark.anyio
    async def test_concurrent_action_throughput(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Measure throughput with concurrent action execution.

        Executes multiple actions in parallel and measures total
        throughput in actions per second.
        """
        resolved_context = resolved_context_factory(role=benchmark_role)
        concurrency_levels = [1, 5, 10, 20]
        results = {}

        total_successes = 0
        for concurrency in concurrency_levels:
            inputs = [simple_action_input_factory() for _ in range(concurrency)]

            start = time.perf_counter()
            tasks = [
                test_backend.execute(
                    input=inp,
                    role=benchmark_role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
                for inp in inputs
            ]
            outcomes = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start

            # Track successes (may be 0 if registry not available)
            successes = sum(1 for r in outcomes if r.type == "success")
            total_successes += successes

            throughput = concurrency / elapsed
            results[concurrency] = throughput

        print("\nConcurrent throughput:")
        for level, throughput in results.items():
            print(f"  Concurrency {level:2d}: {throughput:.1f} actions/sec")

        if total_successes == 0:
            print("  Note: Measured failure path (registry not available)")

        # At least achieve reasonable throughput at each level
        assert results[1] > 5, "Single action throughput too low"
        assert results[10] > 20, "Concurrent throughput at 10 too low"

    @pytest.mark.anyio
    async def test_sustained_throughput(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Measure sustained throughput over many actions.

        Executes a large number of actions to measure sustained
        performance and detect any degradation over time.
        """
        resolved_context = resolved_context_factory(role=benchmark_role)
        total_actions = 100
        batch_size = 10
        batch_times = []
        total_successes = 0

        for _ in range(total_actions // batch_size):
            inputs = [simple_action_input_factory() for _ in range(batch_size)]

            start = time.perf_counter()
            tasks = [
                test_backend.execute(
                    input=inp,
                    role=benchmark_role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
                for inp in inputs
            ]
            outcomes = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start

            successes = sum(1 for r in outcomes if r.type == "success")
            total_successes += successes

            batch_times.append(elapsed)

        # Calculate stats
        total_time = sum(batch_times)
        throughput = total_actions / total_time
        avg_batch_ms = sum(batch_times) / len(batch_times) * 1000

        print(
            f"\nSustained throughput over {total_actions} actions (successes={total_successes}):"
        )
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Throughput: {throughput:.1f} actions/sec")
        print(f"  Avg batch time: {avg_batch_ms:.1f}ms")

        if total_successes == 0:
            print("  Note: Measured failure path (registry not available)")

        # Check for degradation (last batches shouldn't be much slower)
        first_half_avg = sum(batch_times[: len(batch_times) // 2]) / (
            len(batch_times) // 2
        )
        second_half_avg = sum(batch_times[len(batch_times) // 2 :]) / (
            len(batch_times) // 2
        )
        degradation = (second_half_avg - first_half_avg) / first_half_avg

        print(f"  Degradation: {degradation * 100:.1f}%")
        assert degradation < 0.5, f"Performance degraded by {degradation * 100:.1f}%"


# =============================================================================
# Memory Usage Benchmarks
# =============================================================================


class TestMemoryUsage:
    """Benchmarks for memory usage characteristics.

    Measures memory consumption during and after burst workloads
    to detect memory leaks or excessive memory growth.
    """

    @pytest.mark.anyio
    async def test_memory_after_burst(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Measure memory growth after burst workload.

        Executes a burst of actions and measures memory usage
        before and after to detect memory leaks.
        """
        resolved_context = resolved_context_factory(role=benchmark_role)
        # Force garbage collection and get baseline
        gc.collect()
        baseline_mb = get_memory_usage_mb()

        # Execute burst
        burst_size = 100
        inputs = [simple_action_input_factory() for _ in range(burst_size)]

        tasks = [
            test_backend.execute(
                input=inp,
                role=benchmark_role,
                resolved_context=resolved_context,
                timeout=30.0,
            )
            for inp in inputs
        ]
        outcomes = await asyncio.gather(*tasks)

        successes = sum(1 for r in outcomes if r.type == "success")

        # Force garbage collection and measure after
        gc.collect()
        after_burst_mb = get_memory_usage_mb()

        # Let things settle
        await asyncio.sleep(0.5)
        gc.collect()
        settled_mb = get_memory_usage_mb()

        growth_mb = after_burst_mb - baseline_mb
        settled_growth_mb = settled_mb - baseline_mb

        print(
            f"\nMemory usage after {burst_size} action burst (successes={successes}):"
        )
        print(f"  Baseline: {baseline_mb:.1f} MB")
        print(f"  After burst: {after_burst_mb:.1f} MB (+{growth_mb:.1f} MB)")
        print(f"  After GC: {settled_mb:.1f} MB (+{settled_growth_mb:.1f} MB)")

        if successes == 0:
            print("  Note: Measured failure path (registry not available)")

        # Memory shouldn't grow excessively (allow 50MB growth for 100 actions)
        assert growth_mb < 50, f"Memory grew by {growth_mb:.1f} MB during burst"
        # After GC, memory should partially recover
        assert settled_growth_mb < 30, (
            f"Memory leak: {settled_growth_mb:.1f} MB retained"
        )

    @pytest.mark.anyio
    async def test_memory_stability_over_iterations(
        self,
        test_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Test memory stability across multiple burst iterations.

        Runs multiple burst cycles to detect cumulative memory leaks.
        """
        resolved_context = resolved_context_factory(role=benchmark_role)
        gc.collect()
        initial_mb = get_memory_usage_mb()
        memory_readings = [initial_mb]

        burst_size = 50
        iterations = 5

        for _ in range(iterations):
            inputs = [simple_action_input_factory() for _ in range(burst_size)]

            tasks = [
                test_backend.execute(
                    input=inp,
                    role=benchmark_role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )
                for inp in inputs
            ]
            await asyncio.gather(*tasks)

            gc.collect()
            memory_readings.append(get_memory_usage_mb())

        print(
            f"\nMemory across {iterations} burst iterations ({burst_size} actions each):"
        )
        for i, mb in enumerate(memory_readings):
            delta = mb - initial_mb
            print(f"  Iteration {i}: {mb:.1f} MB ({delta:+.1f} MB)")

        # Check for cumulative growth (should not grow linearly)
        total_growth = memory_readings[-1] - initial_mb
        growth_per_iteration = total_growth / iterations

        print(f"  Growth per iteration: {growth_per_iteration:.2f} MB")

        # Memory should stabilize, not grow linearly
        assert growth_per_iteration < 10, (
            f"Memory growing at {growth_per_iteration:.1f} MB/iteration"
        )


# =============================================================================
# Backend Comparison Benchmarks
# =============================================================================


class TestBackendComparison:
    """Comparative benchmarks between all four backends.

    These tests compare performance characteristics between
    test, direct, pool, and ephemeral backends.

    Prerequisites:
    - Registry must be synced with tarballs available in MinIO
    - Run `just dev` and sync registry via UI/API before running
    - Must run inside Docker on Linux (use `just bench` command)

    Backend configurations:
    - test: In-process execution (no sandboxing)
    - direct: Per-action subprocess execution
    - pool: Pooled nsjail workers (full OS-level isolation)
    - ephemeral: Per-action nsjail sandbox (maximum isolation)
    """

    @pytest.mark.anyio
    async def test_all_backends_latency(
        self,
        require_registry_sync: None,  # noqa: ARG002
        test_backend: ExecutorBackend,
        direct_backend: ExecutorBackend,
        pool_backend: ExecutorBackend,
        ephemeral_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Compare action execution latency across all four backends.

        Measures per-action latency for:
        - test: In-process execution
        - direct: Subprocess execution
        - pool: Pooled nsjail workers
        - ephemeral: Per-action nsjail sandbox
        """
        backends = {
            "test": test_backend,
            "direct": direct_backend,
            "pool": pool_backend,
            "ephemeral": ephemeral_backend,
        }

        input_data = simple_action_input_factory()
        resolved_context = resolved_context_factory(role=benchmark_role)
        results: dict[str, dict[str, float]] = {}

        for name, backend in backends.items():

            def make_executor(
                b: ExecutorBackend, ctx: ResolvedContext
            ) -> Callable[[], Awaitable[Any]]:
                return lambda: b.execute(
                    input=input_data,
                    role=benchmark_role,
                    resolved_context=ctx,
                    timeout=30.0,
                )

            times = await run_async_benchmark(
                make_executor(backend, resolved_context),
                rounds=10,
                warmup_rounds=2,
            )

            results[name] = {
                "mean_ms": sum(times) / len(times) * 1000,
                "min_ms": min(times) * 1000,
                "max_ms": max(times) * 1000,
            }

        print("\n" + "=" * 60)
        print("BACKEND LATENCY COMPARISON")
        print("=" * 60)
        print(f"{'Backend':<20} {'Mean':>10} {'Min':>10} {'Max':>10} {'Overhead':>12}")
        print("-" * 60)

        test_mean = results["test"]["mean_ms"]
        for name in ["test", "direct", "pool", "ephemeral"]:
            r = results[name]
            overhead = r["mean_ms"] - test_mean
            overhead_str = f"+{overhead:.1f}ms" if name != "test" else "-"
            print(
                f"{name:<20} {r['mean_ms']:>9.2f}ms {r['min_ms']:>9.2f}ms "
                f"{r['max_ms']:>9.2f}ms {overhead_str:>12}"
            )

        print("=" * 60)

        # Test backend should be fastest (in-process has no IPC overhead)
        assert results["test"]["mean_ms"] <= results["direct"]["mean_ms"], (
            "Test backend should be faster than direct"
        )

    @pytest.mark.anyio
    async def test_all_backends_throughput(
        self,
        require_registry_sync: None,  # noqa: ARG002
        test_backend: ExecutorBackend,
        direct_backend: ExecutorBackend,
        pool_backend: ExecutorBackend,
        ephemeral_backend: ExecutorBackend,
        simple_action_input_factory: Callable[..., RunActionInput],
        resolved_context_factory: Callable[..., ResolvedContext],
        benchmark_role: Role,
    ) -> None:
        """Compare throughput across all four backends.

        Measures actions/second at concurrency=10.
        """
        backends = {
            "test": test_backend,
            "direct": direct_backend,
            "pool": pool_backend,
            "ephemeral": ephemeral_backend,
        }

        resolved_context = resolved_context_factory(role=benchmark_role)
        concurrency = 10
        results: dict[str, float] = {}

        for name, backend in backends.items():
            inputs = [simple_action_input_factory() for _ in range(concurrency)]

            start = time.perf_counter()
            tasks = [
                backend.execute(
                    input=inp,
                    role=benchmark_role,
                    resolved_context=resolved_context,
                    timeout=60.0,
                )
                for inp in inputs
            ]
            await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start

            results[name] = concurrency / elapsed

        print("\n" + "=" * 60)
        print(f"BACKEND THROUGHPUT COMPARISON (concurrency={concurrency})")
        print("=" * 60)
        print(f"{'Backend':<20} {'Throughput':>15}")
        print("-" * 60)

        for name in ["test", "direct", "pool", "ephemeral"]:
            print(f"{name:<20} {results[name]:>12.1f} actions/sec")

        print("=" * 60)

        # Test backend should have highest throughput
        assert results["test"] >= results["ephemeral"], (
            "Test backend should have higher throughput than ephemeral"
        )
