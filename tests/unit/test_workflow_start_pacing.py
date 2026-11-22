"""Workflow start pacing at the service, router and MCP boundaries."""

from __future__ import annotations

import datetime
import time
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from temporalio.client import Client
from temporalio.common import Priority
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat.admission.pacing import UNPACED, StartSlot
from tracecat.api.common import (
    TEMPORAL_UNAVAILABLE_RETRY_AFTER_SECONDS,
)
from tracecat.auth.types import Role
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import InlineObject
from tracecat.webhooks.router import _incoming_webhook
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import (
    WorkflowExecutionsService,
    WorkflowStartTimeoutError,
)

_WF_ID = WorkflowUUID.new_uuid4()
_ORG_ID = uuid.uuid4()
_DSL_CONTENT: dict[str, Any] = {
    "title": "Pacing test",
    "description": "test",
    "entrypoint": {"ref": "start"},
    "actions": [{"ref": "start", "action": "core.noop"}],
    "config": {"enable_runtime_tests": False},
}


def _role() -> Role:
    return Role(
        type="service",
        workspace_id=uuid.uuid4(),
        organization_id=_ORG_ID,
        user_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )


def _about(got: datetime.timedelta, seconds: float) -> bool:
    """Equal within 50 ms: a little time passes between reserve and submit."""
    return abs(got - datetime.timedelta(seconds=seconds)) < datetime.timedelta(
        milliseconds=50
    )


def _definition() -> WorkflowDefinition:
    return cast(
        WorkflowDefinition,
        SimpleNamespace(content=dict(_DSL_CONTENT), registry_lock=None),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock(spec=Client)
    client.start_workflow = AsyncMock(return_value=MagicMock())
    client.execute_workflow = AsyncMock(return_value={"status": "ok"})
    return client


@pytest.fixture
def service(mock_client: MagicMock) -> WorkflowExecutionsService:
    return WorkflowExecutionsService(client=mock_client, role=_role())


@pytest.fixture
def mock_storage() -> Iterator[MagicMock]:
    storage = MagicMock()
    storage.store = AsyncMock(
        return_value=InlineObject(type="inline", data={"_": "ref"})
    )
    with patch(
        "tracecat.workflow.executions.service.get_object_storage",
        return_value=storage,
    ):
        yield storage


@pytest.fixture
def refund() -> AsyncMock:
    return AsyncMock(return_value=None)


@pytest.fixture
def reserve(refund: AsyncMock) -> Iterator[AsyncMock]:
    """Replace the pacer; tests set the delay or refusal on this mock."""
    mock = AsyncMock(return_value=UNPACED)
    with patch(
        "tracecat.workflow.executions.service.StartPacer",
        return_value=SimpleNamespace(reserve=mock, refund=refund),
    ):
        yield mock


def _timeout(
    service: WorkflowExecutionsService, seconds: float | None
) -> Any:  # patch() context manager
    value = datetime.timedelta(seconds=seconds) if seconds is not None else None
    return patch.object(service, "_resolve_execution_timeout", return_value=value)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_passes_delay_and_extends_execution_timeout(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    """Temporal counts start_delay against the execution timeout, so it grows."""
    reserve.return_value = StartSlot(delay=datetime.timedelta(seconds=2))
    with _timeout(service, 10):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
        )

    reserve.assert_awaited_once_with(_ORG_ID)
    kwargs = mock_client.start_workflow.call_args.kwargs
    assert _about(kwargs["start_delay"], 2)
    assert _about(kwargs["execution_timeout"], 12)


@pytest.mark.anyio
async def test_time_spent_before_submission_comes_off_the_delay(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    """Slow trigger-input storage can't collapse paced starts into a burst."""
    # Reserved 1.5 s ago with a 2 s delay: 0.5 s is still owed at submission.
    reserve.return_value = StartSlot(
        delay=datetime.timedelta(seconds=2), reserved_at=time.monotonic() - 1.5
    )
    with _timeout(service, 10):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
        )

    kwargs = mock_client.start_workflow.call_args.kwargs
    assert _about(kwargs["start_delay"], 0.5)
    assert _about(kwargs["execution_timeout"], 10.5)


@pytest.mark.anyio
async def test_unpaced_start_is_unchanged(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    with _timeout(service, 10):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_type=TriggerType.WEBHOOK,
        )

    kwargs = mock_client.start_workflow.call_args.kwargs
    assert "start_delay" not in kwargs
    assert kwargs["execution_timeout"] == datetime.timedelta(seconds=10)


@pytest.mark.anyio
async def test_delay_without_execution_timeout_sets_no_timeout(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    reserve.return_value = StartSlot(delay=datetime.timedelta(seconds=1))
    with _timeout(service, None):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_type=TriggerType.WEBHOOK,
        )

    kwargs = mock_client.start_workflow.call_args.kwargs
    assert _about(kwargs["start_delay"], 1)
    assert "execution_timeout" not in kwargs


@pytest.mark.anyio
async def test_duplicate_start_refunds_its_slot(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
    refund: AsyncMock,
) -> None:
    """A retried start whose run already exists gives its slot back."""
    wf_exec_id = f"{_WF_ID.short()}/exec_fixed"
    mock_client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        wf_exec_id, "DSLWorkflow"
    )
    with _timeout(service, None), pytest.raises(WorkflowAlreadyStartedError):
        await service.create_workflow_execution_wait_for_start(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=wf_exec_id,
            trigger_type=TriggerType.CASE,
        )

    reserve.assert_awaited_once_with(_ORG_ID)
    refund.assert_awaited_once_with(_ORG_ID, reserve.return_value)


@pytest.mark.anyio
async def test_trigger_input_storage_failure_refunds_its_slot(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
    refund: AsyncMock,
) -> None:
    """A start that never reached Temporal gives its slot back."""
    reserve.return_value = StartSlot(delay=datetime.timedelta(seconds=1))
    mock_storage.store.side_effect = OSError("object storage unavailable")
    with _timeout(service, None), pytest.raises(OSError):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
        )

    refund.assert_awaited_once_with(_ORG_ID, reserve.return_value)
    mock_client.start_workflow.assert_not_awaited()


@pytest.mark.anyio
async def test_duplicate_wait_for_result_dispatch_refunds_its_slot(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
    refund: AsyncMock,
) -> None:
    wf_exec_id = f"{_WF_ID.short()}/exec_fixed"
    mock_client.execute_workflow.side_effect = WorkflowAlreadyStartedError(
        wf_exec_id, "DSLWorkflow"
    )
    with _timeout(service, None), pytest.raises(WorkflowAlreadyStartedError):
        await service._dispatch_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=wf_exec_id,
            trigger_type=TriggerType.WEBHOOK,
        )

    refund.assert_awaited_once_with(_ORG_ID, reserve.return_value)


@pytest.mark.anyio
async def test_failed_start_of_unknown_outcome_keeps_its_slot(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
    refund: AsyncMock,
) -> None:
    """A timed-out start may have made a run, so it keeps its slot."""
    mock_client.start_workflow.side_effect = TimeoutError()
    with _timeout(service, None), pytest.raises(TimeoutError):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_type=TriggerType.WEBHOOK,
        )

    refund.assert_not_awaited()


@pytest.mark.anyio
async def test_nowait_start_reserves_once_and_passes_the_delay(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    reserve.return_value = StartSlot(delay=datetime.timedelta(seconds=3))
    scheduled: list[Any] = []
    with (
        _timeout(service, None),
        patch(
            "tracecat.workflow.executions.service.asyncio.ensure_future",
            side_effect=lambda coro: scheduled.append(coro) or MagicMock(),
        ),
    ):
        service.create_workflow_execution_nowait(
            dsl=DSLInput(**_DSL_CONTENT), wf_id=_WF_ID
        )
        assert len(scheduled) == 1
        await scheduled[0]

    reserve.assert_awaited_once()
    kwargs = mock_client.start_workflow.call_args.kwargs
    assert _about(kwargs["start_delay"], 3)


@pytest.mark.anyio
async def test_wait_for_result_dispatch_is_paced(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    reserve.return_value = StartSlot(delay=datetime.timedelta(seconds=4))
    with _timeout(service, 60):
        await service._dispatch_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_type=TriggerType.WEBHOOK,
        )

    kwargs = mock_client.execute_workflow.call_args.kwargs
    assert _about(kwargs["start_delay"], 4)
    assert _about(kwargs["execution_timeout"], 64)


# ---------------------------------------------------------------------------
# Webhook router
# ---------------------------------------------------------------------------


async def _no_delay[T](items: Iterable[T], delay: float) -> AsyncIterator[T]:
    """``cooperative`` without the sleep between NDJSON batches."""
    _ = delay
    for item in items:
        yield item


def _webhook_request(content_type: str) -> MagicMock:
    request = MagicMock(spec=Request)
    request.headers = {"content-type": content_type}
    return request


@pytest.mark.anyio
async def test_ndjson_batches_count_one_start_each(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    """20 NDJSON lines start 3 runs (batches of 8): 3 reservations."""
    payload = [{"event": i} for i in range(20)]
    with (
        _timeout(service, None),
        patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=service),
        ),
        patch("tracecat.webhooks.router.cooperative", _no_delay),
    ):
        await _incoming_webhook(
            workflow_id=_WF_ID,
            defn=_definition(),
            payload=payload,
            echo=False,
            empty_echo=False,
            vendor=None,
            request=_webhook_request("application/x-ndjson"),
            content_type="application/x-ndjson",
        )

    assert reserve.await_count == 3
    assert mock_client.start_workflow.await_count == 3


@pytest.mark.anyio
async def test_webhook_start_timeout_is_503_with_retry_after() -> None:
    exec_service = AsyncMock()
    exec_service.create_workflow_execution_wait_for_start = AsyncMock(
        side_effect=WorkflowStartTimeoutError(
            "Timed out while dispatching workflow to Temporal"
        )
    )
    with (
        patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=exec_service),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _incoming_webhook(
            workflow_id=_WF_ID,
            defn=_definition(),
            payload={"x": 1},
            echo=False,
            empty_echo=False,
            vendor=None,
            request=_webhook_request("application/json"),
            content_type="application/json",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers == {
        "Retry-After": str(TEMPORAL_UNAVAILABLE_RETRY_AFTER_SECONDS)
    }


@pytest.mark.anyio
async def test_webhook_other_timeouts_are_not_a_temporal_503() -> None:
    """A timeout outside the Temporal start (say, storage) isn't a 503."""
    exec_service = AsyncMock()
    exec_service.create_workflow_execution_wait_for_start = AsyncMock(
        side_effect=TimeoutError("object storage timed out")
    )
    with (
        patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=exec_service),
        ),
        pytest.raises(TimeoutError, match="object storage"),
    ):
        await _incoming_webhook(
            workflow_id=_WF_ID,
            defn=_definition(),
            payload={"x": 1},
            echo=False,
            empty_echo=False,
            vendor=None,
            request=_webhook_request("application/json"),
            content_type="application/json",
        )


@pytest.mark.anyio
async def test_start_timeout_raises_workflow_start_timeout(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    mock_client.start_workflow.side_effect = TimeoutError()
    with _timeout(service, None), pytest.raises(WorkflowStartTimeoutError):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_type=TriggerType.WEBHOOK,
        )


@pytest.mark.anyio
async def test_async_and_sync_starts_carry_org_fairness_key(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    """Activities and child workflows inherit the key from these starts."""
    with _timeout(service, None):
        await service._start_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_async",
            trigger_type=TriggerType.WEBHOOK,
        )
        await service._dispatch_workflow(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_sync",
            trigger_type=TriggerType.WEBHOOK,
        )

    expected = Priority(fairness_key=str(_ORG_ID))
    assert mock_client.start_workflow.call_args.kwargs["priority"] == expected
    assert mock_client.execute_workflow.call_args.kwargs["priority"] == expected
