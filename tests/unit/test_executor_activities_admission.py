"""Admission gate wiring in the executor action activity."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.admission.gate import ConcurrencyBusyError
from tracecat.admission.types import (
    ADMISSION_ACK_SIGNAL,
    ConcurrencyBusyResult,
    ConcurrencyResource,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor import activities as executor_activities
from tracecat.executor.activities import ExecutorActivities
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock

STARTED = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


def _run_input(
    *, for_each: str | None = None, agent_session_id: uuid.UUID | None = None
) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    action_name = "core.transform.reshape"
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"value": "ok"},
            ref="limited",
            for_each=for_each,
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
            actions={action_name: "tracecat_registry"},
        ),
        agent_session_id=agent_session_id,
    )


def _info(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "attempt": 2,
        "activity_id": "7",
        "workflow_id": "wf_x/exec_y",
        "workflow_run_id": "run-1",
        "task_queue": "shared-action-queue",
        "started_time": STARTED,
        "scheduled_time": STARTED,
        "start_to_close_timeout": timedelta(seconds=120),
        "schedule_to_close_timeout": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _lease(units: int = 1) -> MagicMock:
    lease = MagicMock()
    lease.units = units
    lease.renew = AsyncMock(return_value=None)
    lease.release = AsyncMock(return_value=None)
    return lease


@pytest.fixture
def mocks() -> Iterator[SimpleNamespace]:
    with (
        patch.object(executor_activities, "activity") as mock_activity,
        patch.object(executor_activities, "get_executor_backend"),
        patch.object(
            executor_activities, "dispatch_action", new_callable=AsyncMock
        ) as dispatch,
        patch.object(
            executor_activities, "materialize_context", new_callable=AsyncMock
        ) as materialize,
        patch.object(
            executor_activities.gate, "acquire", new_callable=AsyncMock
        ) as acquire,
    ):
        mock_activity.info.return_value = _info()
        mock_activity.heartbeat = MagicMock()
        materialize.side_effect = lambda ctx: ctx
        dispatch.return_value = {"ok": True}
        yield SimpleNamespace(
            activity=mock_activity,
            dispatch=dispatch,
            materialize=materialize,
            acquire=acquire,
        )


@pytest.mark.anyio
async def test_busy_returns_result_before_any_effect(
    role: Role, mocks: SimpleNamespace
) -> None:
    mocks.acquire.side_effect = ConcurrencyBusyError(
        resource=ConcurrencyResource.ACTIONS, in_use=4, limit=4
    )

    result = await ExecutorActivities.execute_action_activity(_run_input(), role)

    assert result == ConcurrencyBusyResult(
        resource=ConcurrencyResource.ACTIONS, in_use=4, limit=4, attempt=2
    )
    mocks.materialize.assert_not_awaited()
    mocks.dispatch.assert_not_awaited()
    kwargs = mocks.acquire.await_args.kwargs
    assert kwargs["org_id"] == role.organization_id
    assert kwargs["resource"] is ConcurrencyResource.ACTIONS
    assert kwargs["lease_id"] == "run-1:7:2"
    assert kwargs["units"] == 1
    assert kwargs["deadline"] == STARTED + timedelta(seconds=120)


@pytest.mark.anyio
async def test_agent_tool_calls_are_not_gated(
    role: Role, mocks: SimpleNamespace
) -> None:
    result = await ExecutorActivities.execute_action_activity(
        _run_input(agent_session_id=uuid.uuid4()), role
    )

    assert not isinstance(result, ConcurrencyBusyResult)
    mocks.dispatch.assert_awaited_once()
    mocks.acquire.assert_not_awaited()


@pytest.mark.anyio
async def test_for_each_reserves_its_width_and_runs_no_wider(
    role: Role, mocks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY", 6)
    lease = _lease(units=2)  # the gate clamped 6 to the org's limit of 2
    mocks.acquire.return_value = lease

    await ExecutorActivities.execute_action_activity(
        _run_input(for_each="${{ for var.x in [1, 2, 3] }}"), role
    )

    assert mocks.acquire.await_args.kwargs["units"] == 6
    assert mocks.dispatch.await_args.kwargs["max_concurrency"] == 2
    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_uncounted_run_keeps_the_configured_width(
    role: Role, mocks: SimpleNamespace
) -> None:
    mocks.acquire.return_value = None  # unlimited, or failed open

    await ExecutorActivities.execute_action_activity(
        _run_input(for_each="${{ for var.x in [1, 2, 3] }}"), role
    )

    assert mocks.acquire.await_args.kwargs["units"] > 1
    assert mocks.dispatch.await_args.kwargs["max_concurrency"] is None


@pytest.mark.anyio
async def test_lease_is_released_when_the_action_fails(
    role: Role, mocks: SimpleNamespace
) -> None:
    lease = _lease()
    mocks.acquire.return_value = lease
    mocks.dispatch.side_effect = RuntimeError("boom")

    with pytest.raises(ApplicationError):
        await ExecutorActivities.execute_action_activity(_run_input(), role)

    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_lease_is_released_when_materialization_fails(
    role: Role, mocks: SimpleNamespace
) -> None:
    lease = _lease()
    mocks.acquire.return_value = lease
    mocks.materialize.side_effect = OSError("s3 down")

    with pytest.raises(OSError):
        await ExecutorActivities.execute_action_activity(_run_input(), role)

    lease.release.assert_awaited_once()
    mocks.dispatch.assert_not_awaited()


@pytest.mark.anyio
async def test_renewal_runs_even_with_heartbeats_off(
    role: Role, mocks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__ACTIVITY_HEARTBEAT_INTERVAL", 0)
    monkeypatch.setattr(executor_activities, "LEASE_RENEW_INTERVAL_SECONDS", 0.01)
    lease = _lease()
    mocks.acquire.return_value = lease

    async def slow_dispatch(**_: Any) -> dict[str, bool]:
        await asyncio.sleep(0.08)
        return {"ok": True}

    mocks.dispatch.side_effect = slow_dispatch

    await ExecutorActivities.execute_action_activity(_run_input(), role)

    assert lease.renew.await_count >= 2
    mocks.activity.heartbeat.assert_not_called()
    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_heartbeat_loop_heartbeats_before_renewing() -> None:
    order: list[str] = []

    async def renew() -> None:
        order.append("renew")

    with patch.object(executor_activities, "activity") as mock_activity:
        mock_activity.heartbeat = MagicMock(side_effect=lambda _: order.append("hb"))
        task = asyncio.create_task(
            executor_activities._heartbeat_loop(0.01, "ref", "action", renew=renew)
        )
        await asyncio.sleep(0.035)
        task.cancel()
        await task

    assert order[:4] == ["hb", "renew", "hb", "renew"]


def test_lease_id_and_deadline_fall_back_when_fields_are_missing() -> None:
    info = _info(
        workflow_run_id=None,
        start_to_close_timeout=None,
        schedule_to_close_timeout=timedelta(seconds=30),
    )

    assert executor_activities._lease_id(info) == "wf_x/exec_y:7:2"  # pyright: ignore[reportArgumentType]
    assert executor_activities._lease_deadline(info) == STARTED + timedelta(  # pyright: ignore[reportArgumentType]
        seconds=30
    )


def _probe_input() -> RunActionInput:
    return _run_input().model_copy(update={"ack_admission": True})


@pytest.mark.anyio
async def test_admitted_probe_signals_the_run(
    role: Role, mocks: SimpleNamespace
) -> None:
    mocks.acquire.return_value = _lease()
    handle = MagicMock()
    handle.signal = AsyncMock(return_value=None)
    mocks.activity.client.return_value.get_workflow_handle.return_value = handle

    await ExecutorActivities.execute_action_activity(_probe_input(), role)

    mocks.activity.client.return_value.get_workflow_handle.assert_called_once_with(
        "wf_x/exec_y", run_id="run-1"
    )
    handle.signal.assert_awaited_once_with(ADMISSION_ACK_SIGNAL, "7")


@pytest.mark.anyio
async def test_busy_probe_and_plain_dispatch_send_no_signal(
    role: Role, mocks: SimpleNamespace
) -> None:
    mocks.acquire.return_value = _lease()
    await ExecutorActivities.execute_action_activity(_run_input(), role)

    mocks.acquire.side_effect = ConcurrencyBusyError(
        resource=ConcurrencyResource.ACTIONS, in_use=1, limit=1
    )
    await ExecutorActivities.execute_action_activity(_probe_input(), role)

    mocks.activity.client.assert_not_called()


@pytest.mark.anyio
async def test_failed_admission_signal_does_not_fail_the_action(
    role: Role, mocks: SimpleNamespace
) -> None:
    mocks.acquire.return_value = _lease()
    handle = MagicMock()
    handle.signal = AsyncMock(side_effect=RuntimeError("temporal unreachable"))
    mocks.activity.client.return_value.get_workflow_handle.return_value = handle

    result = await ExecutorActivities.execute_action_activity(_probe_input(), role)

    assert not isinstance(result, ConcurrencyBusyResult)
    mocks.dispatch.assert_awaited_once()


@pytest.mark.anyio
async def test_cancellation_during_admission_signal_releases_the_lease(
    role: Role, mocks: SimpleNamespace
) -> None:
    lease = _lease()
    mocks.acquire.return_value = lease
    handle = MagicMock()
    handle.signal = AsyncMock(side_effect=asyncio.CancelledError())
    mocks.activity.client.return_value.get_workflow_handle.return_value = handle

    with pytest.raises(asyncio.CancelledError):
        await ExecutorActivities.execute_action_activity(_probe_input(), role)

    lease.release.assert_awaited_once()
    mocks.dispatch.assert_not_awaited()
