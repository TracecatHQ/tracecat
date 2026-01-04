"""Warm worker pool with nsjail sandboxing (untrusted mode).

Each worker runs inside a persistent nsjail sandbox. The sandbox starts once
and stays alive, so Python startup + imports happen only once per worker.

Workers execute in UNTRUSTED mode - they do NOT have database credentials.
All secrets, variables, and action metadata are pre-resolved on the host
before being sent to workers via Unix socket.

This gives you:
- nsjail isolation (namespaces, seccomp, resource limits)
- Warm Python (imports done once)
- ~100-200ms overhead instead of ~4000ms
- No DB credentials in sandbox (untrusted mode)

Architecture:

    ┌─────────────────────────────────────────────────────────────────┐
    │ Host Process                                                    │
    │                                                                 │
    │  SandboxedWorkerPool                                            │
    │       │                                                         │
    │       ├──► nsjail[0] ──► pool_worker.py (warm Python)           │
    │       │      │              listening on /work/task.sock        │
    │       │      └── /tmp/sandbox-0/work/ mounted into sandbox      │
    │       │                                                         │
    │       ├──► nsjail[1] ──► pool_worker.py (warm Python)           │
    │       │                                                         │
    │       └──► nsjail[2] ──► pool_worker.py (warm Python)           │
    │                                                                 │
    └─────────────────────────────────────────────────────────────────┘

Communication:
- Each sandbox has a /work directory mounted from host
- Worker creates Unix socket at /work/task.sock
- Host connects to /tmp/sandbox-{id}/work/task.sock
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from pydantic import TypeAdapter

from tracecat import config
from tracecat.executor.schemas import ExecutorResult, ResolvedContext
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput

_ExecutorResultAdapter = TypeAdapter(ExecutorResult)


def get_available_cpus() -> int:
    """Get number of CPUs available to this process.

    Uses sched_getaffinity which respects container CPU limits (cgroups),
    unlike os.cpu_count() which returns total host CPUs.

    Falls back to os.cpu_count() on platforms that don't support affinity.
    """
    # Use getattr to access sched_getaffinity dynamically since it's
    # only available on some Unix systems (Linux), not macOS/Windows
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            # Respects Docker --cpus, K8s CPU limits, cgroups
            return len(sched_getaffinity(0))
        except OSError:
            pass
    # Fallback for platforms without sched_getaffinity (e.g., macOS)
    return os.cpu_count() or 4


@dataclass
class SandboxedWorkerInfo:
    """Metadata about a sandboxed worker."""

    worker_id: int
    pid: int
    process: asyncio.subprocess.Process
    work_dir: Path  # Host path to /work directory
    socket_path: Path  # Host path to socket (inside work_dir)
    active_tasks: int = 0  # Number of concurrent tasks currently running
    tasks_completed: int = 0
    recycling: bool = (
        False  # True when worker is being recycled (don't assign new tasks)
    )
    last_task_completed_at: float = 0.0  # Timestamp of last completed task
    oldest_task_started_at: float = (
        0.0  # Timestamp of oldest active task (for stuck detection)
    )


@dataclass
class SandboxedWorkerPool:
    """Pool of warm workers running inside nsjail sandboxes.

    Each worker is a persistent nsjail process with Python already started
    and imports loaded. Tasks are dispatched via Unix sockets that are
    mounted into the sandbox.

    This gives you both:
    - Security: nsjail namespace isolation, seccomp, resource limits
    - Performance: warm Python, ~100-200ms overhead vs ~4000ms cold

    Each worker can handle multiple concurrent tasks (up to max_concurrent_per_worker)
    via its internal thread pool. This allows better utilization of warm workers.
    """

    size: int = 8
    max_tasks_per_worker: int = 1000
    max_concurrent_per_worker: int = (
        16  # Concurrent tasks per worker (IO-bound friendly)
    )
    startup_timeout: float = 60.0
    memory_limit_mb: int = 512
    _workers: list[SandboxedWorkerInfo] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _started: bool = False
    _base_dir: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir())
        / "tracecat-sandboxed-workers"
    )
    _metrics_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _metrics_interval: float = 10.0  # Emit metrics every 10 seconds
    _stuck_worker_timeout: float = (
        60.0  # Consider worker stuck if task running longer than this
    )
    _tasks_waiting: int = 0  # Number of tasks currently waiting for a worker slot

    # Lock contention metrics
    _lock_acquisitions: int = 0  # Total number of lock acquisitions
    _lock_contentions: int = 0  # Number of times lock was contended (waited > 1ms)
    _lock_wait_time_total_ms: float = 0.0  # Total time spent waiting for lock
    _lock_max_wait_time_ms: float = 0.0  # Maximum wait time for lock

    # Round-robin index for tie-breaking when multiple workers have equal load
    _next_worker_index: int = 0

    # Throughput tracking
    _last_metrics_time: float = 0.0
    _last_total_completed: int = 0

    # Lifetime metrics (persist across worker recycling)
    # Task metrics
    _lifetime_tasks_completed: int = 0
    _lifetime_tasks_failed: int = 0
    _lifetime_tasks_timed_out: int = 0
    _lifetime_tasks_cancelled: int = 0

    # Worker lifecycle metrics
    _lifetime_worker_recycles: int = 0  # Graceful recycles after max_tasks
    _lifetime_worker_kills: int = 0  # Forced SIGKILL after SIGTERM timeout
    _lifetime_worker_crashes: int = 0  # Unexpected worker deaths
    _lifetime_worker_spawn_failures: int = 0  # Failed to spawn replacement

    # Capacity metrics
    _lifetime_tasks_queued: int = 0  # Tasks that had to wait for a worker slot
    _peak_tasks_waiting: int = 0  # Max concurrent tasks waiting at any point

    # Latency metrics (in milliseconds)
    _total_task_time_ms: float = 0.0  # Cumulative for avg calculation
    _max_task_time_ms: float = 0.0  # Slowest task
    _min_task_time_ms: float = float("inf")  # Fastest task

    # Per-worker lifetime stats (keyed by worker_id, persists across recycles)
    _per_worker_tasks_completed: dict[int, int] = field(default_factory=dict)
    _per_worker_recycles: dict[int, int] = field(default_factory=dict)

    @asynccontextmanager
    async def _timed_lock(self):
        """Acquire lock with timing metrics.

        Tracks lock acquisitions, contention (wait > 1ms), and wait times.
        """
        import time

        start = time.monotonic()
        was_locked = self._lock.locked()

        async with self._lock:
            wait_time_ms = (time.monotonic() - start) * 1000
            self._lock_acquisitions += 1
            self._lock_wait_time_total_ms += wait_time_ms

            if wait_time_ms > self._lock_max_wait_time_ms:
                self._lock_max_wait_time_ms = wait_time_ms

            # Consider it contended if we had to wait > 1ms or lock was already held
            if was_locked or wait_time_ms > 1.0:
                self._lock_contentions += 1
                if wait_time_ms > 10.0:  # Log significant waits
                    logger.debug(
                        "Lock contention detected",
                        wait_time_ms=f"{wait_time_ms:.2f}",
                    )

            yield

    async def start(self) -> None:
        """Start the sandboxed worker pool."""
        if self._started:
            return

        # Create base directory
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale directories
        for d in self._base_dir.glob("sandbox-*"):
            if d.is_dir():
                import shutil

                shutil.rmtree(d, ignore_errors=True)

        logger.info(
            "Starting sandboxed worker pool",
            size=self.size,
            max_concurrent_per_worker=self.max_concurrent_per_worker,
            base_dir=str(self._base_dir),
        )

        # Spawn workers
        spawn_tasks = [self._spawn_sandboxed_worker(i) for i in range(self.size)]
        results = await asyncio.gather(*spawn_tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                logger.error("Failed to spawn sandboxed worker", error=str(result))
            else:
                self._workers.append(result)

        if not self._workers:
            raise RuntimeError("Failed to start any sandboxed workers")

        self._started = True
        total_capacity = len(self._workers) * self.max_concurrent_per_worker
        logger.info(
            "Sandboxed worker pool ready",
            num_workers=len(self._workers),
            max_concurrent_per_worker=self.max_concurrent_per_worker,
            total_capacity=total_capacity,
            pids=[w.pid for w in self._workers],
        )

        # Start metrics emission background task
        self._metrics_task = asyncio.create_task(self._emit_metrics_loop())

    async def _spawn_sandboxed_worker(self, worker_id: int) -> SandboxedWorkerInfo:
        """Spawn a new worker (sandboxed with nsjail or direct subprocess)."""
        # Create work directory for this worker
        work_dir = self._base_dir / f"sandbox-{worker_id}" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Socket will be created by the worker
        socket_path = work_dir / "task.sock"
        socket_path.unlink(missing_ok=True)

        # Environment for the worker process
        env = os.environ.copy()
        env["TRACECAT_WORKER_ID"] = str(worker_id)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        if config.TRACECAT__DISABLE_NSJAIL:
            # Direct subprocess mode (no sandbox)
            proc = await self._spawn_direct_worker(
                worker_id, work_dir, socket_path, env
            )
        else:
            # Sandboxed mode with nsjail
            proc = await self._spawn_nsjail_worker(
                worker_id, work_dir, socket_path, env
            )

        # Wait for socket to appear (indicates worker is ready)
        start_time = asyncio.get_event_loop().time()
        while not socket_path.exists():
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.startup_timeout:
                proc.kill()
                await proc.wait()
                stderr = await proc.stderr.read() if proc.stderr else b""
                raise RuntimeError(
                    f"Worker {worker_id} failed to start within "
                    f"{self.startup_timeout}s: {stderr.decode()[:500]}"
                )
            if proc.returncode is not None:
                stderr = await proc.stderr.read() if proc.stderr else b""
                raise RuntimeError(
                    f"Worker {worker_id} died during startup: {stderr.decode()[:500]}"
                )
            await asyncio.sleep(0.1)

        mode = "direct" if config.TRACECAT__DISABLE_NSJAIL else "sandboxed"
        logger.info(
            f"{mode.capitalize()} worker ready",
            worker_id=worker_id,
            pid=proc.pid,
            socket=str(socket_path),
            mode=mode,
        )

        return SandboxedWorkerInfo(
            worker_id=worker_id,
            pid=proc.pid,
            process=proc,
            work_dir=work_dir,
            socket_path=socket_path,
        )

    async def _spawn_direct_worker(
        self,
        worker_id: int,
        work_dir: Path,
        socket_path: Path,
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Spawn worker as direct subprocess (no nsjail sandbox)."""
        import sys

        # Socket path is the actual host path
        env["TRACECAT_WORKER_SOCKET"] = str(socket_path)

        cmd = [
            sys.executable,
            "-m",
            "tracecat.executor.backends.sandboxed_pool.worker",
        ]

        logger.debug(
            "Starting direct worker",
            worker_id=worker_id,
            cmd=cmd,
        )

        return await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
        )

    async def _spawn_nsjail_worker(
        self,
        worker_id: int,
        work_dir: Path,
        socket_path: Path,
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Spawn worker inside nsjail sandbox."""
        # Socket path inside sandbox
        env["TRACECAT_WORKER_SOCKET"] = "/work/task.sock"

        # Build nsjail config
        nsjail_config = self._build_nsjail_config(worker_id, work_dir)
        config_path = work_dir.parent / "nsjail.cfg"
        config_path.write_text(nsjail_config)

        # Get site-packages path for PYTHONPATH
        import site

        site_packages = site.getsitepackages()[0] if site.getsitepackages() else ""
        # Include /app, packages directories, site-packages, and registry cache
        pythonpath_parts = ["/app", "/app/packages/tracecat-registry"]
        if site_packages:
            pythonpath_parts.append(site_packages)
        # Add registry cache for custom registry modules distributed via tarball
        registry_cache_dir = Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
        if registry_cache_dir.exists():
            pythonpath_parts.append(str(registry_cache_dir))
        pythonpath = ":".join(pythonpath_parts)

        # Build nsjail command
        # NOTE: We intentionally do NOT pass DB credentials to the sandbox.
        # Workers execute in untrusted mode with pre-resolved context.
        nsjail_path = config.TRACECAT__SANDBOX_NSJAIL_PATH
        cmd = [
            nsjail_path,
            "--config",
            str(config_path),
            # Pass through required env vars
            "--env",
            "TRACECAT_WORKER_ID",
            "--env",
            "TRACECAT_WORKER_SOCKET",
            # Set PYTHONPATH explicitly for the sandbox Python
            "--env",
            f"PYTHONPATH={pythonpath}",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
        ]

        logger.debug(
            "Starting nsjail worker",
            worker_id=worker_id,
            cmd=cmd[:5],
            config_path=str(config_path),
        )

        # Start nsjail process
        # NOTE: Don't use PIPE for stdout/stderr - if not consumed, the buffer fills
        # and the subprocess blocks on logging, causing deadlock. Instead, let output
        # inherit to parent process so logs appear in container logs.
        return await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
        )

    def _build_nsjail_config(self, worker_id: int, work_dir: Path) -> str:
        """Build nsjail configuration for a persistent worker."""
        rootfs = config.TRACECAT__SANDBOX_ROOTFS_PATH
        tracecat_app = "/app"  # Where tracecat is installed in container

        # Get site-packages path for the executor's venv
        import site

        site_packages = site.getsitepackages()[0] if site.getsitepackages() else ""

        config_lines = [
            'name: "tracecat-worker"',
            f'description: "Warm worker {worker_id}"',
            "",
            "mode: ONCE",
            'hostname: "sandbox"',
            "time_limit: 0",  # No time limit - worker stays alive
            "",
            "# Clone flags for isolation",
            "clone_newnet: false",  # Need network for HTTP requests in actions
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
            "clone_newcgroup: true",
            "",
            "# Resource limits",
            "rlimit_as_type: SOFT",
            "rlimit_cpu_type: SOFT",
            "rlimit_fsize_type: SOFT",
            "rlimit_nofile_type: SOFT",
            "",
            "# Keep CAP_NET_BIND_SERVICE for Unix sockets",
            "keep_caps: false",
            "",
            "# Root filesystem",
            'mount { dst: "/" fstype: "tmpfs" rw: true }',
            "",
            "# System directories from rootfs",
            f'mount {{ src: "{rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
            f'mount {{ src: "{rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
            f'mount {{ src: "{rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
        ]

        # Optional lib64
        lib64 = Path(rootfs) / "lib64"
        if lib64.exists():
            config_lines.append(
                f'mount {{ src: "{rootfs}/lib64" dst: "/lib64" is_bind: true rw: false }}'
            )

        # /etc for DNS resolution
        config_lines.extend(
            [
                "",
                "# /etc from rootfs",
                f'mount {{ src: "{rootfs}/etc" dst: "/etc" is_bind: true rw: false }}',
                "# DNS resolution from host",
                'mount { src: "/etc/resolv.conf" dst: "/etc/resolv.conf" is_bind: true rw: false }',
            ]
        )

        # Tracecat application
        config_lines.extend(
            [
                "",
                "# Tracecat application",
                f'mount {{ src: "{tracecat_app}" dst: "/app" is_bind: true rw: false }}',
            ]
        )

        # Site packages
        if site_packages:
            config_lines.append(
                f'mount {{ src: "{site_packages}" dst: "{site_packages}" is_bind: true rw: false }}'
            )

        # Work directory (RW for socket and IPC)
        config_lines.extend(
            [
                "",
                "# Work directory for socket communication",
                f'mount {{ src: "{work_dir}" dst: "/work" is_bind: true rw: true }}',
            ]
        )

        # Dev and proc
        config_lines.extend(
            [
                "",
                "# /dev essentials",
                'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
                'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
                'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
                "",
                "# /proc and /tmp",
                'mount { src: "/proc" dst: "/proc" is_bind: true rw: false }',
                'mount { dst: "/tmp" fstype: "tmpfs" rw: true }',
            ]
        )

        # Registry cache directory (for custom registry modules)
        # IMPORTANT: This must come AFTER the /tmp tmpfs mount above,
        # otherwise the tmpfs will overwrite this bind mount
        registry_cache_dir = Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
        if registry_cache_dir.exists():
            config_lines.extend(
                [
                    "",
                    "# Registry cache (for custom registry modules)",
                    f'mount {{ src: "{registry_cache_dir}" dst: "{registry_cache_dir}" is_bind: true rw: false }}',
                ]
            )

        # Exec config
        config_lines.extend(
            [
                "",
                "# Execute the pool worker",
                "exec_bin {",
                '  path: "/usr/local/bin/python3"',
                '  arg: "-m"',
                '  arg: "tracecat.executor.backends.sandboxed_pool.worker"',
                "}",
                "",
                'cwd: "/app"',
            ]
        )

        return "\n".join(config_lines)

    async def _get_available_worker(self, timeout: float = 30.0) -> SandboxedWorkerInfo:
        """Get a worker with available capacity.

        Workers can handle multiple concurrent tasks up to max_concurrent_per_worker.
        This method finds a worker with available capacity and increments its active_tasks count.
        """
        start_time = asyncio.get_event_loop().time()
        last_log_time = start_time
        log_interval = 5.0  # Log wait status every 5 seconds
        wait_logged = False
        is_waiting = False  # Track if we've incremented _tasks_waiting

        try:
            while True:
                async with self._timed_lock():
                    # Collect pool state for logging
                    workers_at_capacity = 0
                    workers_dead = 0
                    workers_recycling = 0
                    total_active = 0

                    # Find all available workers and minimum load
                    available_workers: list[SandboxedWorkerInfo] = []
                    min_load = float("inf")
                    for worker in self._workers:
                        total_active += worker.active_tasks
                        if worker.process.returncode is not None:
                            workers_dead += 1
                            continue  # Worker is dead
                        if worker.recycling:
                            workers_recycling += 1
                            continue  # Worker is being recycled
                        if worker.active_tasks >= self.max_concurrent_per_worker:
                            workers_at_capacity += 1
                            continue  # Worker is at capacity
                        available_workers.append(worker)
                        if worker.active_tasks < min_load:
                            min_load = worker.active_tasks

                    if available_workers:
                        # Get candidates with minimum load
                        candidates = [
                            w for w in available_workers if w.active_tasks == min_load
                        ]

                        # Round-robin selection among candidates with equal load
                        # Use modulo to wrap around the candidate list
                        selected_idx = self._next_worker_index % len(candidates)
                        best_worker = candidates[selected_idx]
                        self._next_worker_index = (self._next_worker_index + 1) % len(
                            self._workers
                        )

                        best_worker.active_tasks += 1
                        # Only update oldest_task_started_at when transitioning 0→1 active tasks
                        # This ensures stuck detection tracks the oldest running task, not the newest
                        if best_worker.active_tasks == 1:
                            best_worker.oldest_task_started_at = time.monotonic()
                        # Log worker selection with load info
                        worker_loads = [
                            f"w{w.worker_id}:{w.active_tasks}"
                            for w in self._workers
                            if w.process.returncode is None and not w.recycling
                        ]
                        logger.debug(
                            "Worker selected",
                            selected_worker=best_worker.worker_id,
                            selected_worker_active=best_worker.active_tasks,
                            selected_worker_completed=best_worker.tasks_completed,
                            worker_loads=",".join(worker_loads),
                            total_active=total_active,
                        )
                        if wait_logged:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            # Decrement waiting count before returning
                            if is_waiting:
                                self._tasks_waiting = max(0, self._tasks_waiting - 1)
                                is_waiting = False
                            logger.info(
                                "Worker slot acquired after waiting",
                                worker_id=best_worker.worker_id,
                                wait_time_s=f"{elapsed:.2f}",
                                tasks_still_waiting=self._tasks_waiting,
                            )
                        return best_worker

                    # No worker available - increment waiting count on first wait
                    if not is_waiting:
                        self._tasks_waiting += 1
                        is_waiting = True
                        self._lifetime_tasks_queued += 1
                        # Track peak waiting
                        if self._tasks_waiting > self._peak_tasks_waiting:
                            self._peak_tasks_waiting = self._tasks_waiting

                    # Collect state for logging (include waiting count)
                    pool_state = {
                        "total_workers": len(self._workers),
                        "workers_at_capacity": workers_at_capacity,
                        "workers_dead": workers_dead,
                        "workers_recycling": workers_recycling,
                        "total_active_tasks": total_active,
                        "max_capacity": len(self._workers)
                        * self.max_concurrent_per_worker,
                        "tasks_waiting": self._tasks_waiting,
                    }

                current_time = asyncio.get_event_loop().time()
                elapsed = current_time - start_time

                # Log on first wait and periodically thereafter
                if not wait_logged:
                    wait_logged = True
                    logger.warning(
                        "No available worker, waiting for slot",
                        timeout_s=timeout,
                        **pool_state,
                    )
                    last_log_time = current_time
                elif current_time - last_log_time >= log_interval:
                    logger.warning(
                        "Still waiting for worker slot",
                        elapsed_s=f"{elapsed:.1f}",
                        remaining_s=f"{timeout - elapsed:.1f}",
                        **pool_state,
                    )
                    last_log_time = current_time

                if elapsed > timeout:
                    logger.error(
                        "Timeout waiting for worker slot",
                        elapsed_s=f"{elapsed:.1f}",
                        **pool_state,
                    )
                    raise RuntimeError(
                        "No available sandboxed worker (all at capacity)"
                    )

                await asyncio.sleep(0.01)
        finally:
            # Always decrement waiting count if we were waiting (must be inside lock)
            if is_waiting:
                async with self._timed_lock():
                    self._tasks_waiting = max(0, self._tasks_waiting - 1)
                    logger.debug(
                        "Task stopped waiting (timeout/cancelled)",
                        tasks_waiting=self._tasks_waiting,
                    )

    async def _emit_metrics_loop(self) -> None:
        """Background task that periodically emits pool metrics."""
        while self._started:
            try:
                await asyncio.sleep(self._metrics_interval)
                if not self._started:
                    break
                await self._emit_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error emitting pool metrics", error=str(e))

    async def _emit_metrics(self) -> None:
        """Emit current pool metrics."""
        now = time.monotonic()
        elapsed_since_last = (
            now - self._last_metrics_time if self._last_metrics_time > 0 else 0
        )

        async with self._timed_lock():
            total_active = 0
            total_completed = 0
            workers_alive = 0
            workers_dead = 0
            workers_recycling = 0
            worker_stats: list[dict[str, Any]] = []
            stuck_workers: list[dict[str, Any]] = []

            for worker in self._workers:
                is_alive = worker.process.returncode is None
                if is_alive:
                    workers_alive += 1
                else:
                    workers_dead += 1
                if worker.recycling:
                    workers_recycling += 1

                total_active += worker.active_tasks
                total_completed += worker.tasks_completed

                # Calculate time since oldest task started (for active workers)
                task_running_s: float | None = None
                if worker.active_tasks > 0 and worker.oldest_task_started_at > 0:
                    task_running_s = now - worker.oldest_task_started_at

                worker_stat: dict[str, Any] = {
                    "id": worker.worker_id,
                    "active": worker.active_tasks,
                    "completed": worker.tasks_completed,
                    "alive": int(is_alive),
                    "recycling": int(worker.recycling),
                }

                # Include task duration for workers with active tasks
                if task_running_s is not None:
                    worker_stat["task_running_s"] = f"{task_running_s:.1f}"
                    # Flag workers with tasks running longer than threshold
                    if task_running_s > self._stuck_worker_timeout:
                        stuck_workers.append(
                            {
                                "worker_id": worker.worker_id,
                                "active_tasks": worker.active_tasks,
                                "running_s": f"{task_running_s:.1f}",
                            }
                        )

                worker_stats.append(worker_stat)

            total_capacity = len(self._workers) * self.max_concurrent_per_worker
            utilization = (
                (total_active / total_capacity * 100) if total_capacity > 0 else 0
            )

        # Compute lock contention stats
        avg_lock_wait_ms = (
            self._lock_wait_time_total_ms / self._lock_acquisitions
            if self._lock_acquisitions > 0
            else 0.0
        )
        lock_contention_pct = (
            self._lock_contentions / self._lock_acquisitions * 100
            if self._lock_acquisitions > 0
            else 0.0
        )

        # Compute throughput
        completed_delta = total_completed - self._last_total_completed
        throughput_per_sec = (
            completed_delta / elapsed_since_last if elapsed_since_last > 0 else 0.0
        )

        # Update tracking for next interval
        self._last_metrics_time = now
        self._last_total_completed = total_completed

        metrics = {
            "pool_size": len(self._workers),
            "total_capacity": total_capacity,
            "total_active": total_active,
            "tasks_waiting": self._tasks_waiting,
            "utilization_pct": f"{utilization:.1f}",
            "total_completed": total_completed,
            # Throughput metrics
            "completed_delta": completed_delta,
            "throughput_per_sec": f"{throughput_per_sec:.2f}",
            "workers_alive": workers_alive,
            "workers_dead": workers_dead,
            "workers_recycling": workers_recycling,
            "worker_stats": worker_stats,
            # Lock contention metrics
            "lock_acquisitions": self._lock_acquisitions,
            "lock_contentions": self._lock_contentions,
            "lock_contention_pct": f"{lock_contention_pct:.1f}",
            "lock_wait_total_ms": f"{self._lock_wait_time_total_ms:.2f}",
            "lock_wait_avg_ms": f"{avg_lock_wait_ms:.3f}",
            "lock_wait_max_ms": f"{self._lock_max_wait_time_ms:.2f}",
        }

        logger.info(f"Pool metrics: {json.dumps(metrics, indent=2)}")

        # Emit warning for potentially stuck workers
        if stuck_workers:
            logger.warning(
                f"Potentially stuck workers detected (tasks running > {self._stuck_worker_timeout}s)",
                stuck_workers=stuck_workers,
                total_stuck=len(stuck_workers),
            )

    async def _release_worker(self, worker: SandboxedWorkerInfo) -> None:
        """Release a worker back to the pool.

        Decrements active_tasks and recycles the worker if it has completed
        max_tasks_per_worker and has no other active tasks.
        """
        should_recycle = False

        async with self._timed_lock():
            worker.active_tasks = max(0, worker.active_tasks - 1)
            worker.tasks_completed += 1
            worker.last_task_completed_at = time.monotonic()

            # Reset oldest_task_started_at when worker becomes idle
            if worker.active_tasks == 0:
                worker.oldest_task_started_at = 0.0

            # Update lifetime metrics
            self._lifetime_tasks_completed += 1
            self._per_worker_tasks_completed[worker.worker_id] = (
                self._per_worker_tasks_completed.get(worker.worker_id, 0) + 1
            )

            # Only recycle when worker is idle and has exceeded task limit
            if (
                worker.tasks_completed >= self.max_tasks_per_worker
                and worker.active_tasks == 0
                and not worker.recycling  # Prevent double-recycling
            ):
                # Mark as recycling so no new tasks are assigned to this worker
                worker.recycling = True
                should_recycle = True

        # Perform recycle outside the lock to avoid blocking the pool
        if should_recycle:
            await self._recycle_worker(worker)

    async def _recycle_worker(self, worker: SandboxedWorkerInfo) -> None:
        """Recycle a sandboxed worker.

        This method is called outside the lock to avoid blocking the pool.
        It terminates the old worker, spawns a new one, then acquires the lock
        briefly to update the workers list.
        """
        logger.info(
            "Recycling sandboxed worker",
            worker_id=worker.worker_id,
            tasks_completed=worker.tasks_completed,
        )

        # Track recycle
        self._lifetime_worker_recycles += 1
        self._per_worker_recycles[worker.worker_id] = (
            self._per_worker_recycles.get(worker.worker_id, 0) + 1
        )

        # Terminate the old worker (outside lock - this can take time)
        was_killed = False
        try:
            worker.process.terminate()
            await asyncio.wait_for(worker.process.wait(), timeout=5.0)
        except TimeoutError:
            worker.process.kill()
            await worker.process.wait()
            was_killed = True
            self._lifetime_worker_kills += 1

        logger.debug(
            "Old worker terminated",
            worker_id=worker.worker_id,
            was_killed=was_killed,
        )

        # Spawn new worker (outside lock - this can take up to startup_timeout)
        new_worker: SandboxedWorkerInfo | None = None
        try:
            new_worker = await self._spawn_sandboxed_worker(worker.worker_id)
        except Exception as e:
            self._lifetime_worker_spawn_failures += 1
            logger.error(
                "Failed to spawn replacement worker",
                worker_id=worker.worker_id,
                error=str(e),
            )

        # Update workers list (briefly acquire lock)
        async with self._timed_lock():
            try:
                idx = self._workers.index(worker)
                if new_worker is not None:
                    self._workers[idx] = new_worker
                else:
                    # Failed to spawn replacement, remove the dead worker
                    self._workers.pop(idx)
            except ValueError:
                # Worker was already removed (shouldn't happen, but be safe)
                logger.warning(
                    "Worker not found in list during recycle",
                    worker_id=worker.worker_id,
                )

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute a task on an available sandboxed worker.

        Args:
            input: The RunActionInput containing task and execution context
            role: The Role for authorization
            resolved_context: Pre-resolved secrets, variables, and action impl
            timeout: Execution timeout in seconds
        """
        if not self._started:
            raise RuntimeError("Sandboxed worker pool not started")

        action_name = input.task.action
        task_ref = input.task.ref

        logger.debug(
            "Acquiring worker for task",
            action=action_name,
            task_ref=task_ref,
        )

        worker = await self._get_available_worker()

        logger.info(
            "Worker acquired for task",
            worker_id=worker.worker_id,
            action=action_name,
            task_ref=task_ref,
            worker_active_tasks=worker.active_tasks,
            worker_completed=worker.tasks_completed,
        )

        start_time = time.monotonic()
        try:
            result = await self._execute_on_worker(
                worker, input, role, resolved_context, timeout
            )
            # Track latency for successful execution
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._total_task_time_ms += elapsed_ms
            if elapsed_ms > self._max_task_time_ms:
                self._max_task_time_ms = elapsed_ms
            if elapsed_ms < self._min_task_time_ms:
                self._min_task_time_ms = elapsed_ms
            return result
        except asyncio.CancelledError:
            self._lifetime_tasks_cancelled += 1
            logger.warning(
                "Task cancelled during execution",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                worker_active_tasks=worker.active_tasks,
            )
            raise
        except TimeoutError:
            self._lifetime_tasks_timed_out += 1
            logger.error(
                "Task timed out during execution",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                timeout=timeout,
            )
            raise
        except Exception as e:
            self._lifetime_tasks_failed += 1
            logger.error(
                "Task failed during execution",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
        finally:
            logger.debug(
                "Releasing worker after task",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
            )
            try:
                await self._release_worker(worker)
                logger.debug(
                    "Worker released successfully",
                    worker_id=worker.worker_id,
                    action=action_name,
                    task_ref=task_ref,
                    worker_active_tasks=worker.active_tasks,
                )
            except asyncio.CancelledError:
                # Even if cancelled, we must release the worker
                logger.warning(
                    "Cancellation during worker release - forcing release",
                    worker_id=worker.worker_id,
                    action=action_name,
                    task_ref=task_ref,
                )
                # Force decrement without async lock
                worker.active_tasks = max(0, worker.active_tasks - 1)
                worker.tasks_completed += 1
                # Reset oldest_task_started_at when worker becomes idle
                if worker.active_tasks == 0:
                    worker.oldest_task_started_at = 0.0
                raise

    async def _execute_on_worker(
        self,
        worker: SandboxedWorkerInfo,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float,
    ) -> ExecutorResult:
        """Execute task on a sandboxed worker via Unix socket."""

        start_time = time.monotonic()
        action_name = input.task.action
        task_ref = input.task.ref
        stage = "init"

        request = {
            "input": input.model_dump(mode="json"),
            "role": role.model_dump(mode="json"),
            "resolved_context": resolved_context.model_dump(mode="json"),
        }
        request_bytes = orjson.dumps(request)

        logger.debug(
            "Executing on worker - starting",
            worker_id=worker.worker_id,
            action=action_name,
            task_ref=task_ref,
            request_size=len(request_bytes),
            socket_path=str(worker.socket_path),
        )

        writer: asyncio.StreamWriter | None = None
        try:
            # Connect to worker's socket (on host filesystem)
            stage = "connect"
            logger.debug(
                "Connecting to worker socket",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
            )
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(worker.socket_path)),
                timeout=5.0,
            )
            connect_elapsed = (time.monotonic() - start_time) * 1000
            logger.debug(
                "Connected to worker socket",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                connect_ms=f"{connect_elapsed:.1f}",
            )

            # Send request
            stage = "send"
            length_prefix = len(request_bytes).to_bytes(4, "big")
            writer.write(length_prefix + request_bytes)
            await writer.drain()
            send_elapsed = (time.monotonic() - start_time) * 1000
            logger.debug(
                "Request sent to worker",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                send_ms=f"{send_elapsed:.1f}",
            )

            # Read response length
            stage = "read_length"
            length_bytes = await asyncio.wait_for(
                reader.readexactly(4),
                timeout=timeout,
            )
            response_length = int.from_bytes(length_bytes, "big")
            read_length_elapsed = (time.monotonic() - start_time) * 1000
            logger.debug(
                "Response length received",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                response_length=response_length,
                read_length_ms=f"{read_length_elapsed:.1f}",
            )

            # Read response body
            stage = "read_body"
            response_bytes = await asyncio.wait_for(
                reader.readexactly(response_length),
                timeout=timeout,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000
            data = orjson.loads(response_bytes)

            # Validate using discriminated union
            result = _ExecutorResultAdapter.validate_python(data)

            logger.info(
                "Sandboxed worker task completed",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                elapsed_ms=f"{elapsed_ms:.1f}",
                success=result.type == "success",
            )

            return result

        except TimeoutError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Sandboxed worker task timed out",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                stage=stage,
                timeout=timeout,
                elapsed_ms=f"{elapsed_ms:.1f}",
                active_tasks=worker.active_tasks,
            )
            # Don't recycle immediately - other concurrent tasks may still be running.
            # The worker slot will be released in the finally block of execute().
            # If the worker is truly stuck, it will eventually be recycled when
            # tasks_completed exceeds max_tasks_per_worker and active_tasks reaches 0.
            raise

        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Sandboxed worker task cancelled",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                stage=stage,
                elapsed_ms=f"{elapsed_ms:.1f}",
                active_tasks=worker.active_tasks,
            )
            raise

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Sandboxed worker task failed",
                worker_id=worker.worker_id,
                action=action_name,
                task_ref=task_ref,
                stage=stage,
                elapsed_ms=f"{elapsed_ms:.1f}",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        finally:
            # Always close the writer to release the socket connection
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass  # Ignore errors during cleanup

    def get_lifetime_metrics(self) -> dict[str, Any]:
        """Get all lifetime metrics for the pool.

        Returns a dict with all tracked metrics that persist across worker recycling.
        Useful for testing and observability.
        """
        avg_task_time_ms = (
            self._total_task_time_ms / self._lifetime_tasks_completed
            if self._lifetime_tasks_completed > 0
            else 0.0
        )

        return {
            # Task metrics
            "tasks_completed": self._lifetime_tasks_completed,
            "tasks_failed": self._lifetime_tasks_failed,
            "tasks_timed_out": self._lifetime_tasks_timed_out,
            "tasks_cancelled": self._lifetime_tasks_cancelled,
            # Worker lifecycle metrics
            "worker_recycles": self._lifetime_worker_recycles,
            "worker_kills": self._lifetime_worker_kills,
            "worker_crashes": self._lifetime_worker_crashes,
            "worker_spawn_failures": self._lifetime_worker_spawn_failures,
            # Capacity metrics
            "tasks_queued": self._lifetime_tasks_queued,
            "peak_tasks_waiting": self._peak_tasks_waiting,
            # Latency metrics (ms)
            "total_task_time_ms": self._total_task_time_ms,
            "avg_task_time_ms": avg_task_time_ms,
            "max_task_time_ms": self._max_task_time_ms,
            "min_task_time_ms": (
                self._min_task_time_ms
                if self._min_task_time_ms != float("inf")
                else 0.0
            ),
            # Per-worker stats
            "per_worker_tasks_completed": dict(self._per_worker_tasks_completed),
            "per_worker_recycles": dict(self._per_worker_recycles),
        }

    async def shutdown(self) -> None:
        """Shutdown all sandboxed workers."""
        logger.info("Shutting down sandboxed worker pool")

        # Cancel metrics task first
        if self._metrics_task is not None:
            self._metrics_task.cancel()
            try:
                await self._metrics_task
            except asyncio.CancelledError:
                pass
            self._metrics_task = None

        for worker in self._workers:
            try:
                worker.process.terminate()
                await asyncio.wait_for(worker.process.wait(), timeout=5.0)
            except TimeoutError:
                worker.process.kill()
                await worker.process.wait()
            except Exception as e:
                logger.warning(
                    "Error shutting down sandboxed worker",
                    worker_id=worker.worker_id,
                    error=str(e),
                )

        # Clean up work directories
        import shutil

        for worker in self._workers:
            shutil.rmtree(worker.work_dir.parent, ignore_errors=True)

        self._workers.clear()
        self._started = False


# Global pool
_sandboxed_pool: SandboxedWorkerPool | None = None
_sandboxed_pool_lock = asyncio.Lock()


async def get_sandboxed_worker_pool(size: int | None = None) -> SandboxedWorkerPool:
    """Get or create the global sandboxed worker pool.

    Pool size is determined by (in order of precedence):
    1. Explicit `size` parameter
    2. TRACECAT__EXECUTOR_WORKER_POOL_SIZE env var
    3. Auto-sized based on available CPUs (respects container limits)
    """
    global _sandboxed_pool

    async with _sandboxed_pool_lock:
        if _sandboxed_pool is None:
            if size is not None:
                pool_size = size
            elif env_size := os.environ.get("TRACECAT__EXECUTOR_WORKER_POOL_SIZE"):
                pool_size = int(env_size)
            else:
                # Auto-size based on available CPUs
                pool_size = get_available_cpus()
                logger.info(
                    "Auto-sizing pool based on available CPUs",
                    available_cpus=pool_size,
                )

            _sandboxed_pool = SandboxedWorkerPool(size=pool_size)
            await _sandboxed_pool.start()

        return _sandboxed_pool


async def shutdown_sandboxed_worker_pool() -> None:
    """Shutdown the global sandboxed worker pool."""
    global _sandboxed_pool

    async with _sandboxed_pool_lock:
        if _sandboxed_pool is not None:
            await _sandboxed_pool.shutdown()
            _sandboxed_pool = None
