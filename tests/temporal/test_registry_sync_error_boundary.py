"""Registry sync failures keep their diagnostic across the Temporal boundary."""

from __future__ import annotations

import gc
import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment

from tracecat.dsl._converter import get_data_converter
from tracecat.registry.sync.base_service import _workflow_failure_detail
from tracecat.registry.sync.runner import ActionDiscoveryError
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.workflow import (
    RegistrySyncWorkflow,
    sync_registry_activity,
)
from tracecat.temporal.errors import extract_error_classifications
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    gc.collect()
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as environment:
        yield environment


@pytest.mark.anyio
async def test_retryable_sync_failure_reaches_caller_through_interceptor(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow -> interceptor -> Temporal -> API keeps a redacted diagnostic."""
    secret = "Bearer synthetic-registry-token-value"
    attempts = 0

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            nonlocal attempts
            attempts += 1
            raise ActionDiscoveryError(
                "Failed to discover actions: Sandboxed registry discovery "
                f"failed: RuntimeError: worker lost (Authorization: {secret})"
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )
    task_queue = f"registry-sync-boundary-{uuid.uuid4()}"
    workflow_id = f"registry-sync-{uuid.uuid4()}"
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    async with test_worker_factory(
        env.client,
        activities=[sync_registry_activity],
        workflows=[RegistrySyncWorkflow],
        task_queue=task_queue,
    ):
        handle = await env.client.start_workflow(
            RegistrySyncWorkflow.run,
            request,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                initial_interval=timedelta(seconds=5),
            ),
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    failure = exc_info.value
    detail = _workflow_failure_detail(
        failure,
        extract_error_classifications(failure, include_implicit_context=False),
    )
    assert detail.startswith("Registry action discovery failed: ")
    assert "RuntimeError: worker lost" in detail
    assert "unclassified" not in detail
    assert "synthetic-registry-token-value" not in detail

    history = await handle.fetch_history()
    assert "synthetic-registry-token-value" not in history.to_json()
    # Activity retries are exhausted once; the workflow is not rerun.
    assert attempts == 3
    latest = await env.client.get_workflow_handle(workflow_id).describe()
    assert latest.run_id == handle.result_run_id
