"""Tests for the WorkerPool.

These tests cover the pool management logic without requiring actual nsjail execution.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.backends.pool import (
    WorkerInfo,
    WorkerPool,
)
from tracecat.executor.backends.pool.pool import get_available_cpus
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def mock_run_action_input() -> RunActionInput:
    """Create a mock RunActionInput for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="core.http_request",
            args={"url": "https://example.com"},
            ref="test_action",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={"core.http_request": "tracecat_registry"},
        ),
    )


@pytest.fixture
def mock_worker_info() -> WorkerInfo:
    """Create a mock WorkerInfo."""
    proc = MagicMock()
    proc.returncode = None  # Worker is alive
    proc.pid = 12345
    return WorkerInfo(
        worker_id=0,
        pid=12345,
        process=proc,
        work_dir=Path("/tmp/sandbox-0/work"),
        socket_path=Path("/tmp/sandbox-0/work/task.sock"),
        active_tasks=0,
        tasks_completed=0,
    )


@pytest.fixture
def mock_resolved_context() -> ResolvedContext:
    """Create a mock ResolvedContext for testing."""
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(type="udf", module="test", name="mock"),
        evaluated_args={},
        workspace_id="test-workspace",
        workflow_id="test-workflow",
        run_id="test-run",
        executor_token="",
    )


class TestGetAvailableCpus:
    """Tests for get_available_cpus function."""

    def test_returns_positive_integer(self):
        """Should return a positive integer."""
        cpus = get_available_cpus()
        assert isinstance(cpus, int)
        assert cpus > 0

    def test_fallback_when_sched_getaffinity_unavailable(self):
        """Should fallback to os.cpu_count() when sched_getaffinity unavailable."""
        # This test just verifies the function doesn't crash
        # On macOS, sched_getaffinity is not available
        cpus = get_available_cpus()
        assert cpus >= 1


class TestWorkerPool:
    """Tests for WorkerPool class."""

    @pytest.mark.anyio
    async def test_pool_initialization(self):
        """Test pool can be created with default settings."""
        pool = WorkerPool(size=2)
        assert pool.size == 2
        assert pool.max_tasks_per_worker == 1000
        assert pool.max_concurrent_per_worker == 16
        assert not pool._started
        assert len(pool._workers) == 0

    @pytest.mark.anyio
    async def test_pool_not_started_raises_on_execute(
        self, mock_run_action_input, mock_role, mock_resolved_context
    ):
        """Execute should raise if pool not started."""
        pool = WorkerPool(size=2)
        with pytest.raises(RuntimeError, match="not started"):
            await pool.execute(
                input=mock_run_action_input,
                role=mock_role,
                resolved_context=mock_resolved_context,
            )

    @pytest.mark.anyio
    async def test_worker_selection_round_robin(self):
        """Test round-robin selection among workers with equal load."""
        pool = WorkerPool(size=3)
        pool._started = True

        # Create mock workers with same load
        for i in range(3):
            proc = MagicMock()
            proc.returncode = None
            pool._workers.append(
                WorkerInfo(
                    worker_id=i,
                    pid=1000 + i,
                    process=proc,
                    work_dir=Path(f"/tmp/sandbox-{i}/work"),
                    socket_path=Path(f"/tmp/sandbox-{i}/work/task.sock"),
                    active_tasks=0,
                    tasks_completed=0,
                )
            )

        # Get workers multiple times - should round-robin
        worker1 = await pool._get_available_worker(timeout=1.0)
        assert worker1.active_tasks == 1
        first_worker_id = worker1.worker_id

        # Reset active_tasks to simulate release
        worker1.active_tasks = 0

        worker2 = await pool._get_available_worker(timeout=1.0)
        assert worker2.worker_id != first_worker_id  # Different worker selected

    @pytest.mark.anyio
    async def test_worker_selection_least_loaded(self):
        """Test selection of least-loaded worker."""
        pool = WorkerPool(size=3)
        pool._started = True

        # Create workers with different loads
        for i in range(3):
            proc = MagicMock()
            proc.returncode = None
            pool._workers.append(
                WorkerInfo(
                    worker_id=i,
                    pid=1000 + i,
                    process=proc,
                    work_dir=Path(f"/tmp/sandbox-{i}/work"),
                    socket_path=Path(f"/tmp/sandbox-{i}/work/task.sock"),
                    active_tasks=i * 2,  # 0, 2, 4
                    tasks_completed=0,
                )
            )

        # Should select the one with 0 active tasks
        selected = await pool._get_available_worker(timeout=1.0)
        # Worker 0 had 0 active tasks, now has 1
        assert selected.worker_id == 0
        assert pool._workers[0].active_tasks == 1

    @pytest.mark.anyio
    async def test_worker_selection_skips_dead_workers(self):
        """Test that dead workers are not selected."""
        pool = WorkerPool(size=2)
        pool._started = True

        # Create one dead worker and one alive
        dead_proc = MagicMock()
        dead_proc.returncode = 1  # Dead
        pool._workers.append(
            WorkerInfo(
                worker_id=0,
                pid=1000,
                process=dead_proc,
                work_dir=Path("/tmp/sandbox-0/work"),
                socket_path=Path("/tmp/sandbox-0/work/task.sock"),
                active_tasks=0,
                tasks_completed=0,
            )
        )

        alive_proc = MagicMock()
        alive_proc.returncode = None  # Alive
        pool._workers.append(
            WorkerInfo(
                worker_id=1,
                pid=1001,
                process=alive_proc,
                work_dir=Path("/tmp/sandbox-1/work"),
                socket_path=Path("/tmp/sandbox-1/work/task.sock"),
                active_tasks=0,
                tasks_completed=0,
            )
        )

        worker = await pool._get_available_worker(timeout=1.0)
        assert worker.worker_id == 1  # The alive worker

    @pytest.mark.anyio
    async def test_worker_selection_skips_recycling_workers(self):
        """Test that workers being recycled are not selected."""
        pool = WorkerPool(size=2)
        pool._started = True

        # Create one recycling worker and one available
        for i in range(2):
            proc = MagicMock()
            proc.returncode = None
            pool._workers.append(
                WorkerInfo(
                    worker_id=i,
                    pid=1000 + i,
                    process=proc,
                    work_dir=Path(f"/tmp/sandbox-{i}/work"),
                    socket_path=Path(f"/tmp/sandbox-{i}/work/task.sock"),
                    active_tasks=0,
                    tasks_completed=0,
                    recycling=(i == 0),  # First worker is recycling
                )
            )

        worker = await pool._get_available_worker(timeout=1.0)
        assert worker.worker_id == 1  # The non-recycling worker

    @pytest.mark.anyio
    async def test_worker_selection_timeout_when_all_at_capacity(self):
        """Test timeout when all workers are at capacity."""
        pool = WorkerPool(size=1, max_concurrent_per_worker=1)
        pool._started = True

        proc = MagicMock()
        proc.returncode = None
        pool._workers.append(
            WorkerInfo(
                worker_id=0,
                pid=1000,
                process=proc,
                work_dir=Path("/tmp/sandbox-0/work"),
                socket_path=Path("/tmp/sandbox-0/work/task.sock"),
                active_tasks=1,  # At capacity
                tasks_completed=0,
            )
        )

        with pytest.raises(RuntimeError, match="No available worker"):
            await pool._get_available_worker(timeout=0.1)

    @pytest.mark.anyio
    async def test_release_worker_decrements_active_tasks(self):
        """Test that releasing a worker decrements its active_tasks."""
        pool = WorkerPool(size=1)
        pool._started = True

        proc = MagicMock()
        proc.returncode = None
        worker = WorkerInfo(
            worker_id=0,
            pid=1000,
            process=proc,
            work_dir=Path("/tmp/sandbox-0/work"),
            socket_path=Path("/tmp/sandbox-0/work/task.sock"),
            active_tasks=3,
            tasks_completed=0,
        )
        pool._workers.append(worker)

        await pool._release_worker(worker)

        assert worker.active_tasks == 2
        assert worker.tasks_completed == 1

    @pytest.mark.anyio
    async def test_release_worker_triggers_recycle_at_limit(self):
        """Test that releasing triggers recycle when at task limit and idle."""
        pool = WorkerPool(size=1, max_tasks_per_worker=10)
        pool._started = True

        proc = MagicMock()
        proc.returncode = None
        worker = WorkerInfo(
            worker_id=0,
            pid=1000,
            process=proc,
            work_dir=Path("/tmp/sandbox-0/work"),
            socket_path=Path("/tmp/sandbox-0/work/task.sock"),
            active_tasks=1,  # Will become 0 after release
            tasks_completed=9,  # Will become 10 (>= limit)
        )
        pool._workers.append(worker)

        # Mock _recycle_worker to track if it's called
        with patch.object(
            pool, "_recycle_worker", new_callable=AsyncMock
        ) as mock_recycle:
            await pool._release_worker(worker)
            mock_recycle.assert_called_once_with(worker)
            assert worker.recycling is True

    @pytest.mark.anyio
    async def test_release_worker_no_recycle_if_active_tasks_remain(self):
        """Test that recycle is not triggered if active tasks remain."""
        pool = WorkerPool(size=1, max_tasks_per_worker=10)
        pool._started = True

        proc = MagicMock()
        proc.returncode = None
        worker = WorkerInfo(
            worker_id=0,
            pid=1000,
            process=proc,
            work_dir=Path("/tmp/sandbox-0/work"),
            socket_path=Path("/tmp/sandbox-0/work/task.sock"),
            active_tasks=2,  # Will become 1 after release (still active)
            tasks_completed=9,  # Will become 10
        )
        pool._workers.append(worker)

        with patch.object(
            pool, "_recycle_worker", new_callable=AsyncMock
        ) as mock_recycle:
            await pool._release_worker(worker)
            mock_recycle.assert_not_called()
            assert worker.recycling is False

    @pytest.mark.anyio
    async def test_concurrent_worker_acquisition(self):
        """Test that concurrent acquisitions are handled correctly."""
        pool = WorkerPool(size=2, max_concurrent_per_worker=2)
        pool._started = True

        # Create workers
        for i in range(2):
            proc = MagicMock()
            proc.returncode = None
            pool._workers.append(
                WorkerInfo(
                    worker_id=i,
                    pid=1000 + i,
                    process=proc,
                    work_dir=Path(f"/tmp/sandbox-{i}/work"),
                    socket_path=Path(f"/tmp/sandbox-{i}/work/task.sock"),
                    active_tasks=0,
                    tasks_completed=0,
                )
            )

        # Acquire multiple workers concurrently
        async def acquire():
            return await pool._get_available_worker(timeout=1.0)

        acquired_workers = await asyncio.gather(*[acquire() for _ in range(4)])

        # All 4 acquisitions should succeed
        assert len(acquired_workers) == 4

        # Should have 4 workers assigned (2 workers x 2 concurrent)
        total_active = sum(w.active_tasks for w in pool._workers)
        assert total_active == 4

    @pytest.mark.anyio
    async def test_lock_contention_metrics(self):
        """Test that lock contention metrics are tracked."""
        pool = WorkerPool(size=1)
        pool._started = True

        proc = MagicMock()
        proc.returncode = None
        pool._workers.append(
            WorkerInfo(
                worker_id=0,
                pid=1000,
                process=proc,
                work_dir=Path("/tmp/sandbox-0/work"),
                socket_path=Path("/tmp/sandbox-0/work/task.sock"),
                active_tasks=0,
                tasks_completed=0,
            )
        )

        initial_acquisitions = pool._lock_acquisitions

        await pool._get_available_worker(timeout=1.0)

        assert pool._lock_acquisitions > initial_acquisitions


class TestWorkerInfo:
    """Tests for WorkerInfo dataclass."""

    def test_default_values(self):
        """Test default field values."""
        proc = MagicMock()
        proc.pid = 12345
        info = WorkerInfo(
            worker_id=0,
            pid=12345,
            process=proc,
            work_dir=Path("/tmp/work"),
            socket_path=Path("/tmp/work/task.sock"),
        )
        assert info.active_tasks == 0
        assert info.tasks_completed == 0
        assert info.recycling is False
        assert info.last_task_completed_at == 0.0
        assert info.oldest_task_started_at == 0.0
