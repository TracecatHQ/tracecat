from __future__ import annotations

from types import SimpleNamespace

import pytest

from tracecat.executor import worker


class _ImmediateInterruptEvent:
    async def wait(self) -> None:
        return None


class _FakeThreadPoolExecutor:
    def __init__(self, *, max_workers: int) -> None:
        self.max_workers = max_workers

    def __enter__(self) -> _FakeThreadPoolExecutor:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None


class _FakeWorker:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> _FakeWorker:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


def _fake_activity(name: str):
    async def _activity() -> None:
        return None

    setattr(_activity, "__temporal_activity_definition", SimpleNamespace(name=name))
    return _activity


@pytest.mark.anyio
async def test_worker_marks_ready_only_after_temporal_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []

    def _clear_ready_file() -> None:
        call_order.append("clear")

    async def _initialize_backend() -> None:
        call_order.append("initialize_backend")

    async def _shutdown_backend() -> None:
        call_order.append("shutdown_backend")

    async def _warmup() -> SimpleNamespace:
        call_order.append("warmup")
        return SimpleNamespace(enabled=False, skipped_reason="warmup_disabled")

    async def _temporal_client() -> object:
        call_order.append("temporal_connect")
        return object()

    def _mark_ready() -> None:
        call_order.append("mark_ready")

    monkeypatch.setattr(worker, "clear_warm_ready_file", _clear_ready_file)
    monkeypatch.setattr(worker, "initialize_executor_backend", _initialize_backend)
    monkeypatch.setattr(worker, "shutdown_executor_backend", _shutdown_backend)
    monkeypatch.setattr(worker, "warm_registry_cache_on_startup", _warmup)
    monkeypatch.setattr(worker, "get_temporal_client", _temporal_client)
    monkeypatch.setattr(worker, "mark_warm_ready", _mark_ready)
    monkeypatch.setattr(worker, "ThreadPoolExecutor", _FakeThreadPoolExecutor)
    monkeypatch.setattr(worker, "Worker", _FakeWorker)
    monkeypatch.setattr(worker, "new_sandbox_runner", lambda: object())
    monkeypatch.setattr(worker, "interrupt_event", _ImmediateInterruptEvent())
    monkeypatch.setattr(
        worker.ExecutorActivities,
        "get_activities",
        classmethod(lambda _cls: [_fake_activity("execute_action_activity")]),
    )
    monkeypatch.setattr(
        worker.RegistrySyncActivities,
        "get_activities",
        classmethod(lambda _cls: [_fake_activity("sync_registry_activity")]),
    )

    await worker.main()

    assert "mark_ready" in call_order
    assert call_order.index("temporal_connect") < call_order.index("mark_ready")
    assert call_order[-1] == "shutdown_backend"


@pytest.mark.anyio
async def test_worker_does_not_mark_ready_when_temporal_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []

    monkeypatch.setattr(worker, "clear_warm_ready_file", lambda: None)
    monkeypatch.setattr(
        worker,
        "initialize_executor_backend",
        lambda: _record_async(call_order, "initialize_backend"),
    )
    monkeypatch.setattr(
        worker,
        "shutdown_executor_backend",
        lambda: _record_async(call_order, "shutdown_backend"),
    )
    monkeypatch.setattr(
        worker,
        "warm_registry_cache_on_startup",
        lambda: _warmup_disabled(call_order),
    )
    monkeypatch.setattr(
        worker, "mark_warm_ready", lambda: call_order.append("mark_ready")
    )

    async def _temporal_client_failure() -> object:
        call_order.append("temporal_connect")
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(worker, "get_temporal_client", _temporal_client_failure)

    with pytest.raises(RuntimeError, match="temporal unavailable"):
        await worker.main()

    assert "mark_ready" not in call_order
    assert "shutdown_backend" in call_order


async def _record_async(call_order: list[str], marker: str) -> None:
    call_order.append(marker)


async def _warmup_disabled(call_order: list[str]) -> SimpleNamespace:
    call_order.append("warmup")
    return SimpleNamespace(enabled=False, skipped_reason="warmup_disabled")
