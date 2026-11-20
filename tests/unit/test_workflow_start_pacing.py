"""Workflow start pacing at the service, router and MCP boundaries."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat.admission.pacing import UNPACED, StartRateLimited, StartSlot
from tracecat.api.common import (
    TEMPORAL_UNAVAILABLE_RETRY_AFTER_SECONDS,
    start_rate_limited_exception_handler,
)
from tracecat.auth.types import Role
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import InlineObject
from tracecat.webhooks.router import _incoming_webhook
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService

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


def _refusal() -> StartRateLimited:
    return StartRateLimited(
        retry_after=datetime.timedelta(seconds=2.2), rate=5, burst=10
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
    assert kwargs["start_delay"] == datetime.timedelta(seconds=2)
    assert kwargs["execution_timeout"] == datetime.timedelta(seconds=12)


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
    assert kwargs["start_delay"] == datetime.timedelta(seconds=1)
    assert "execution_timeout" not in kwargs


@pytest.mark.anyio
async def test_refused_start_stores_nothing_and_starts_nothing(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    mock_storage: MagicMock,
    reserve: AsyncMock,
) -> None:
    reserve.side_effect = _refusal()
    with _timeout(service, None), pytest.raises(StartRateLimited):
        await service.create_workflow_execution_wait_for_start(
            dsl=DSLInput(**_DSL_CONTENT),
            wf_id=_WF_ID,
            payload={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
        )

    mock_storage.store.assert_not_awaited()
    mock_client.start_workflow.assert_not_awaited()


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
@pytest.mark.parametrize(
    "method_name",
    ["create_workflow_execution_nowait", "create_draft_workflow_execution_nowait"],
)
async def test_nowait_start_refuses_before_scheduling(
    service: WorkflowExecutionsService,
    mock_client: MagicMock,
    reserve: AsyncMock,
    method_name: str,
) -> None:
    """A refused fire-and-forget start reaches the caller, not a background task."""
    reserve.side_effect = _refusal()
    method = getattr(service, method_name)
    with (
        patch(
            "tracecat.workflow.executions.service.asyncio.ensure_future"
        ) as ensure_future,
        pytest.raises(StartRateLimited),
    ):
        await method(dsl=DSLInput(**_DSL_CONTENT), wf_id=_WF_ID)

    ensure_future.assert_not_called()


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
        await service.create_workflow_execution_nowait(
            dsl=DSLInput(**_DSL_CONTENT), wf_id=_WF_ID
        )
        assert len(scheduled) == 1
        await scheduled[0]

    reserve.assert_awaited_once()
    kwargs = mock_client.start_workflow.call_args.kwargs
    assert kwargs["start_delay"] == datetime.timedelta(seconds=3)


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
    assert kwargs["start_delay"] == datetime.timedelta(seconds=4)
    assert kwargs["execution_timeout"] == datetime.timedelta(seconds=64)


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
        side_effect=TimeoutError("Timed out while dispatching workflow to Temporal")
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
async def test_webhook_rate_limited_start_is_429_with_retry_after() -> None:
    """The webhook lets StartRateLimited through; the API handler makes it 429."""
    exec_service = AsyncMock()
    exec_service.create_workflow_execution_wait_for_start = AsyncMock(
        side_effect=_refusal()
    )
    app = FastAPI()
    app.add_exception_handler(StartRateLimited, start_rate_limited_exception_handler)

    async def webhook(request: Request) -> Any:
        return await _incoming_webhook(
            workflow_id=_WF_ID,
            defn=_definition(),
            payload={"x": 1},
            echo=False,
            empty_echo=False,
            vendor=None,
            request=request,
            content_type="application/json",
        )

    app.add_api_route("/webhook", webhook, methods=["POST"])
    with patch(
        "tracecat.webhooks.router.WorkflowExecutionsService.connect",
        AsyncMock(return_value=exec_service),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/webhook", json={"x": 1})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    detail = response.json()["detail"]
    assert detail["code"] == "workflow_start_rate_limited"
    assert detail["retry_after_seconds"] == 3
    # Webhook callers are outside the org: the body leaves out its limits.
    assert detail["message"] == (
        "Workflow start rate limit reached. Retry after 3 seconds."
    )


def test_api_app_maps_start_rate_limited() -> None:
    from tracecat.api.app import create_app

    app = create_app()
    assert StartRateLimited in app.exception_handlers
