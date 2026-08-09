"""Process-owned event loop for application async I/O.

Temporal runs synchronous activities in a thread pool. Those threads must not
create their own long-lived event loops for async application resources such as
aiobotocore clients. ``AppAsyncRuntime`` owns one loop in one dedicated thread
and provides cancellation-aware bridges for both sync and async callers.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future, wait
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import Any


class AppAsyncRuntimeState(StrEnum):
    """Lifecycle state for an ``AppAsyncRuntime``."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AppAsyncRuntimeHealth:
    """Thread-safe runtime health snapshot."""

    state: AppAsyncRuntimeState
    ready: bool
    thread_alive: bool
    loop_running: bool
    pending_submissions: int


type AsyncCleanup = Callable[[], Coroutine[Any, Any, None]]
_SYNC_RESULT_POLL_SECONDS = 0.1


class AppAsyncRuntime:
    """Own one application event loop in a dedicated thread.

    The runtime is deliberately explicit: callers start it before accepting
    work, submit application I/O to it, then stop intake and drain all accepted
    submissions before closing loop-owned resources and joining the thread.
    """

    def __init__(
        self,
        *,
        name: str = "tracecat-app-io",
        loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    ) -> None:
        self._name = name
        self._loop_factory = loop_factory
        self._state = AppAsyncRuntimeState.NEW
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._failure: BaseException | None = None
        self._pending: set[Future[Any]] = set()

    @property
    def state(self) -> AppAsyncRuntimeState:
        """Return the current lifecycle state."""
        with self._lock:
            return self._state

    @property
    def health(self) -> AppAsyncRuntimeHealth:
        """Return a non-blocking health snapshot."""
        with self._lock:
            thread = self._thread
            loop = self._loop
            return AppAsyncRuntimeHealth(
                state=self._state,
                ready=self._ready.is_set(),
                thread_alive=thread is not None and thread.is_alive(),
                loop_running=loop is not None and loop.is_running(),
                pending_submissions=len(self._pending),
            )

    @property
    def is_healthy(self) -> bool:
        """Return whether the runtime is ready to accept submissions."""
        health = self.health
        return (
            health.state is AppAsyncRuntimeState.RUNNING
            and health.ready
            and health.thread_alive
            and health.loop_running
        )

    @property
    def thread_id(self) -> int | None:
        """Return the dedicated runtime thread identifier when started."""
        with self._lock:
            return self._thread_id

    def start(self, *, timeout: float = 10.0) -> None:
        """Start the dedicated loop thread and wait until it is running."""
        with self._lock:
            if self._state is not AppAsyncRuntimeState.NEW:
                raise RuntimeError(
                    f"App async runtime cannot start from state {self._state.value}"
                )
            self._state = AppAsyncRuntimeState.STARTING
            thread = threading.Thread(
                target=self._run_loop,
                name=self._name,
                daemon=False,
            )
            self._thread = thread
            thread.start()

        if not self._ready.wait(timeout=timeout):
            with self._lock:
                self._state = AppAsyncRuntimeState.FAILED
                self._failure = TimeoutError(
                    f"App async runtime did not become ready within {timeout} seconds"
                )
                loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=timeout)
            raise RuntimeError("App async runtime failed to start") from self._failure

        with self._lock:
            started = self._state is AppAsyncRuntimeState.RUNNING
            failure = self._failure
        if not started:
            thread.join(timeout=timeout)
            raise RuntimeError("App async runtime failed to start") from failure

    def _run_loop(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = self._loop_factory()
            asyncio.set_event_loop(loop)
            with self._lock:
                self._loop = loop
                self._thread_id = threading.get_ident()
            loop.call_soon(self._mark_running)
            loop.run_forever()
            with self._lock:
                stopping = self._state is AppAsyncRuntimeState.STOPPING
            if not stopping:
                # Only close() stops the loop on purpose. Anything else means
                # the runtime silently lost its I/O loop and must report it.
                self._record_failure(
                    RuntimeError("App async runtime loop stopped outside close()")
                )
        except BaseException as exc:
            self._record_failure(exc)
            self._ready.set()
        finally:
            try:
                if loop is not None:
                    self._shutdown_loop(loop)
            except BaseException as exc:
                self._record_failure(exc)
            finally:
                try:
                    asyncio.set_event_loop(None)
                except BaseException as exc:
                    self._record_failure(exc)
                with self._lock:
                    if self._state is not AppAsyncRuntimeState.FAILED:
                        self._state = AppAsyncRuntimeState.STOPPED

    def _record_failure(self, exc: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = exc
            self._state = AppAsyncRuntimeState.FAILED

    def _mark_running(self) -> None:
        with self._lock:
            if self._state is AppAsyncRuntimeState.STARTING:
                self._state = AppAsyncRuntimeState.RUNNING
        self._ready.set()

    @staticmethod
    def _shutdown_loop(loop: asyncio.AbstractEventLoop) -> None:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()

    def _discard_submission(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.discard(future)

    @staticmethod
    async def _drain_loop_tasks() -> None:
        """Wait for submitted tasks whose thread-safe Future was cancelled."""
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _close_rejected_coroutine(coro: Coroutine[Any, Any, object]) -> None:
        coro.close()

    def _submit[T](
        self,
        coro: Coroutine[Any, Any, T],
        *,
        allow_stopping: bool = False,
    ) -> Future[T]:
        with self._lock:
            accepted_states = (
                {AppAsyncRuntimeState.RUNNING, AppAsyncRuntimeState.STOPPING}
                if allow_stopping
                else {AppAsyncRuntimeState.RUNNING}
            )
            loop = self._loop
            if self._state not in accepted_states or loop is None:
                self._close_rejected_coroutine(coro)
                raise RuntimeError(
                    "App async runtime is not accepting submissions "
                    f"(state={self._state.value})"
                )
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
            except BaseException:
                self._close_rejected_coroutine(coro)
                raise
            self._pending.add(future)
            future.add_done_callback(self._discard_submission)
            return future

    def submit[T](self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        """Submit a coroutine and return a thread-safe future."""
        return self._submit(coro)

    def _assert_blocking_submission_allowed(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError(
            "AppAsyncRuntime.run_sync() cannot block an event-loop thread; "
            "use await run_async() instead"
        )

    def run_sync[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit from synchronous code and block only the caller thread.

        If Temporal injects cancellation into a synchronous activity while it
        is blocked in ``Future.result()``, the submitted app-loop task is
        cancelled before the exception is propagated.
        """
        try:
            self._assert_blocking_submission_allowed()
        except BaseException:
            self._close_rejected_coroutine(coro)
            raise

        future = self._submit(coro)
        try:
            while True:
                try:
                    # Temporal cancels a synchronous threaded activity by
                    # injecting CancelledError into its Python thread. Polling
                    # keeps the thread from sleeping indefinitely in the
                    # condition variable, so the exception is delivered and
                    # the app-loop Future is cancelled promptly.
                    return future.result(timeout=_SYNC_RESULT_POLL_SECONDS)
                except TimeoutError:
                    # A completed coroutine may itself raise TimeoutError.
                    # Re-read it without a timeout so that exception propagates
                    # instead of being mistaken for this bridge's poll tick.
                    if future.done():
                        return future.result()
                    with self._lock:
                        state = self._state
                        failure = self._failure
                    if state in (
                        AppAsyncRuntimeState.FAILED,
                        AppAsyncRuntimeState.STOPPED,
                    ):
                        raise RuntimeError(
                            "App async runtime stopped while a submission was pending"
                        ) from failure
        except BaseException:
            future.cancel()
            raise

    async def run_async[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit from async code without blocking the caller event loop."""
        with self._lock:
            runtime_loop = self._loop
        if runtime_loop is asyncio.get_running_loop():
            return await coro

        future = self._submit(coro)
        try:
            return await asyncio.wrap_future(future)
        except BaseException:
            future.cancel()
            raise

    def close(
        self,
        *,
        cleanup: AsyncCleanup | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Stop intake, drain submissions, clean up, and join the loop thread."""
        with self._lock:
            runtime_thread = self._thread
        if threading.current_thread() is runtime_thread:
            raise RuntimeError("App async runtime cannot close from its own thread")

        with self._close_lock:
            self._close_locked(cleanup=cleanup, timeout=timeout)

    def _close_locked(
        self,
        *,
        cleanup: AsyncCleanup | None,
        timeout: float,
    ) -> None:
        """Serialize close calls while performing ordered loop shutdown."""

        with self._lock:
            if self._state is AppAsyncRuntimeState.NEW:
                self._state = AppAsyncRuntimeState.STOPPED
                return
            if self._state is AppAsyncRuntimeState.STOPPED:
                return
            if self._state is AppAsyncRuntimeState.STARTING:
                raise RuntimeError("App async runtime cannot close while starting")
            if self._state is AppAsyncRuntimeState.RUNNING:
                self._state = AppAsyncRuntimeState.STOPPING
            # A failed runtime has already lost its loop, so draining and
            # loop-owned cleanup are impossible; only join and report.
            failed = self._state is AppAsyncRuntimeState.FAILED
            loop = self._loop
            thread = self._thread
            pending = list(self._pending)

        cleanup_error: BaseException | None = None
        try:
            if failed:
                # There is no live loop left to finish accepted submissions.
                # Cancel their thread-safe Futures so blocked callers wake up,
                # then join the owner thread and report the original failure.
                for future in pending:
                    future.cancel()
            elif pending:
                wait(pending)
            if not failed and loop is not None and loop.is_running():
                drain_future = self._submit(
                    self._drain_loop_tasks(),
                    allow_stopping=True,
                )
                drain_future.result()
            if (
                not failed
                and cleanup is not None
                and loop is not None
                and loop.is_running()
            ):
                cleanup_future = self._submit(cleanup(), allow_stopping=True)
                cleanup_future.result()
        except BaseException as exc:
            cleanup_error = exc
        finally:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=timeout)

        if thread is not None and thread.is_alive():
            raise RuntimeError(
                f"App async runtime thread did not stop within {timeout} seconds"
            )
        if cleanup_error is not None:
            raise cleanup_error
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise RuntimeError("App async runtime failed during shutdown") from failure

    async def aclose(
        self,
        *,
        cleanup: AsyncCleanup | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Asynchronously close without blocking the caller event loop."""
        await asyncio.to_thread(self.close, cleanup=cleanup, timeout=timeout)


_APP_ASYNC_RUNTIME: AppAsyncRuntime | None = None
_APP_ASYNC_RUNTIME_LOCK = threading.RLock()


def get_app_async_runtime() -> AppAsyncRuntime | None:
    """Return the installed process runtime, if any."""
    with _APP_ASYNC_RUNTIME_LOCK:
        return _APP_ASYNC_RUNTIME


def install_app_async_runtime(runtime: AppAsyncRuntime) -> None:
    """Install the worker-owned runtime for application I/O routing."""
    global _APP_ASYNC_RUNTIME
    with _APP_ASYNC_RUNTIME_LOCK:
        if _APP_ASYNC_RUNTIME is not None and _APP_ASYNC_RUNTIME is not runtime:
            raise RuntimeError("A different app async runtime is already installed")
        _APP_ASYNC_RUNTIME = runtime


def uninstall_app_async_runtime(runtime: AppAsyncRuntime) -> None:
    """Remove the installed runtime if it matches ``runtime``."""
    global _APP_ASYNC_RUNTIME
    with _APP_ASYNC_RUNTIME_LOCK:
        if _APP_ASYNC_RUNTIME is runtime:
            _APP_ASYNC_RUNTIME = None


@contextmanager
def use_app_async_runtime(runtime: AppAsyncRuntime) -> Iterator[AppAsyncRuntime]:
    """Install a runtime for the duration of a worker lifecycle."""
    install_app_async_runtime(runtime)
    try:
        yield runtime
    finally:
        uninstall_app_async_runtime(runtime)


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the installed app runtime from synchronous code."""
    runtime = get_app_async_runtime()
    if runtime is None:
        coro.close()
        raise RuntimeError("No app async runtime is installed")
    return runtime.run_sync(coro)


async def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Route a coroutine through the app runtime when one is installed."""
    runtime = get_app_async_runtime()
    if runtime is None:
        return await coro
    return await runtime.run_async(coro)


def run_on_app_async_runtime[**P, T](
    func: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Route an async function through the installed app runtime."""

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return await run_async(func(*args, **kwargs))

    return wrapper
