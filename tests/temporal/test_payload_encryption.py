"""Temporal e2e coverage for application-layer payload encryption."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any

import orjson
import pytest
from temporalio import activity, workflow
from temporalio.api.common.v1 import Payload
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.temporal.codec import (
    TRACECAT_TEMPORAL_ENCODING,
    TRACECAT_TEMPORAL_GLOBAL_SCOPE,
    reset_temporal_payload_codec_cache,
    reset_temporal_payload_secret_cache,
)

pytestmark = [pytest.mark.temporal]

WORKSPACE_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
WORKSPACE_SCOPE = str(WORKSPACE_ID).encode()
GLOBAL_SCOPE = TRACECAT_TEMPORAL_GLOBAL_SCOPE.encode()


@pytest.fixture
async def encrypted_env(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[WorkflowEnvironment, None]:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        orjson.dumps(
            {
                "current_key_id": "v1",
                "keys": {"v1": "temporal-e2e-test-root-key"},
            }
        ).decode(),
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN", None)
    reset_temporal_payload_codec_cache()
    reset_temporal_payload_secret_cache()

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(compression_enabled=False),
    ) as workflow_env:
        yield workflow_env

    reset_temporal_payload_codec_cache()
    reset_temporal_payload_secret_cache()


@activity.defn(name="encrypted_echo_activity")
async def encrypted_echo_activity(payload: dict[str, str]) -> dict[str, str]:
    return {
        "activity_message": payload["message"],
        "activity_secret": payload["secret"],
    }


@activity.defn(name="encrypted_failing_activity")
async def encrypted_failing_activity(payload: dict[str, str]) -> None:
    raise ApplicationError(
        f"encrypted failure for {payload['message']}",
        type="EncryptedFailure",
        non_retryable=True,
    )


@workflow.defn
class EncryptedChildWorkflow:
    @workflow.run
    async def run(self, message: str) -> str:
        return f"child:{message}"


@workflow.defn
class EncryptedRoundTripWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> dict[str, Any]:
        activity_result = await workflow.execute_activity(
            encrypted_echo_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=10),
        )
        child_result = await workflow.execute_child_workflow(
            EncryptedChildWorkflow.run,
            payload["message"],
            id=f"{workflow.info().workflow_id}-child",
            task_queue=workflow.info().task_queue,
            execution_timeout=timedelta(seconds=10),
            memo={"encrypted_memo": payload["secret"]},
        )
        return {
            "workflow_message": payload["message"],
            "activity": activity_result,
            "child": child_result,
        }


@workflow.defn
class EncryptedFailureWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> None:
        await workflow.execute_activity(
            encrypted_failing_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def _collect_payloads_by_source(handle: Any) -> dict[str, list[Payload]]:
    payloads_by_source: dict[str, list[Payload]] = {
        "workflow_input": [],
        "activity_input": [],
        "activity_result": [],
        "child_input": [],
        "child_memo": [],
        "child_result": [],
        "workflow_result": [],
        "failure_attributes": [],
    }

    async for event in handle.fetch_history_events():
        match event.event_type:
            case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                attrs = event.workflow_execution_started_event_attributes
                payloads_by_source["workflow_input"].extend(attrs.input.payloads)
            case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                attrs = event.activity_task_scheduled_event_attributes
                payloads_by_source["activity_input"].extend(attrs.input.payloads)
            case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                attrs = event.activity_task_completed_event_attributes
                payloads_by_source["activity_result"].extend(attrs.result.payloads)
            case EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                attrs = event.start_child_workflow_execution_initiated_event_attributes
                payloads_by_source["child_input"].extend(attrs.input.payloads)
                payloads_by_source["child_memo"].extend(attrs.memo.fields.values())
            case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
                attrs = event.child_workflow_execution_completed_event_attributes
                payloads_by_source["child_result"].extend(attrs.result.payloads)
            case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                attrs = event.workflow_execution_completed_event_attributes
                payloads_by_source["workflow_result"].extend(attrs.result.payloads)
            case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                attrs = event.activity_task_failed_event_attributes
                payloads_by_source["failure_attributes"].extend(
                    _failure_encoded_attributes(attrs.failure)
                )

    return payloads_by_source


def _failure_encoded_attributes(failure: Any) -> list[Payload]:
    payloads: list[Payload] = []
    current = failure
    while current is not None:
        if current.HasField("encoded_attributes"):
            payloads.append(current.encoded_attributes)
        current = current.cause if current.HasField("cause") else None
    return payloads


def _assert_encrypted(payloads: list[Payload]) -> None:
    assert payloads
    for payload in payloads:
        assert payload.metadata.get("encoding") == TRACECAT_TEMPORAL_ENCODING
        assert payload.metadata.get("tracecat_key_id") == b"v1"
        assert payload.metadata.get("tracecat_workspace_id") in {
            WORKSPACE_SCOPE,
            GLOBAL_SCOPE,
        }


@pytest.mark.anyio
async def test_encrypted_temporal_payloads_round_trip_and_store_encrypted_history(
    encrypted_env: WorkflowEnvironment,
) -> None:
    task_queue = "test-encrypted-temporal-payload-round-trip"
    workflow_id = "test-encrypted-temporal-payload-round-trip"
    token = ctx_role.set(
        Role(
            type="service",
            service_id="tracecat-service",
            workspace_id=WORKSPACE_ID,
        )
    )
    try:
        async with Worker(
            encrypted_env.client,
            task_queue=task_queue,
            activities=[encrypted_echo_activity],
            workflows=[EncryptedRoundTripWorkflow, EncryptedChildWorkflow],
            workflow_runner=new_sandbox_runner(),
        ):
            result = await encrypted_env.client.execute_workflow(
                EncryptedRoundTripWorkflow.run,
                {"message": "hello", "secret": "sensitive-value"},
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=15),
            )
    finally:
        ctx_role.reset(token)

    assert result == {
        "workflow_message": "hello",
        "activity": {
            "activity_message": "hello",
            "activity_secret": "sensitive-value",
        },
        "child": "child:hello",
    }

    payloads_by_source = await _collect_payloads_by_source(
        encrypted_env.client.get_workflow_handle(workflow_id)
    )

    for source in (
        "workflow_input",
        "activity_input",
        "activity_result",
        "child_input",
        "child_memo",
        "child_result",
        "workflow_result",
    ):
        _assert_encrypted(payloads_by_source[source])

    assert (
        payloads_by_source["workflow_input"][0].metadata.get("tracecat_workspace_id")
        == WORKSPACE_SCOPE
    )


@pytest.mark.anyio
async def test_encrypted_temporal_failure_attributes_are_encoded(
    encrypted_env: WorkflowEnvironment,
) -> None:
    task_queue = "test-encrypted-temporal-failure-attributes"
    workflow_id = "test-encrypted-temporal-failure-attributes"
    token = ctx_role.set(
        Role(
            type="service",
            service_id="tracecat-service",
            workspace_id=WORKSPACE_ID,
        )
    )
    try:
        async with Worker(
            encrypted_env.client,
            task_queue=task_queue,
            activities=[encrypted_failing_activity],
            workflows=[EncryptedFailureWorkflow],
            workflow_runner=new_sandbox_runner(),
        ):
            with pytest.raises(WorkflowFailureError):
                await encrypted_env.client.execute_workflow(
                    EncryptedFailureWorkflow.run,
                    {"message": "boom", "secret": "sensitive-value"},
                    id=workflow_id,
                    task_queue=task_queue,
                    execution_timeout=timedelta(seconds=15),
                )
    finally:
        ctx_role.reset(token)

    payloads_by_source = await _collect_payloads_by_source(
        encrypted_env.client.get_workflow_handle(workflow_id)
    )

    _assert_encrypted(payloads_by_source["workflow_input"])
    _assert_encrypted(payloads_by_source["activity_input"])
    _assert_encrypted(payloads_by_source["failure_attributes"])
