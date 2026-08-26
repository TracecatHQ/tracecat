"""Replay compatibility tests for DSLWorkflow histories."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Never

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from tracecat.auth.types import Role
from tracecat.dsl import workflow as workflow_module
from tracecat.dsl.action import DSLActivities, PrepareSubflowActivityInput
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.types import TaskExceptionInfo
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import (
    ERROR_OWNER_AFTER_HANDLER_PATCH,
    ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH,
    DSLWorkflow,
)
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import extract_error_envelope
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = [pytest.mark.temporal, pytest.mark.integration]


def _activity_name(activity_def: object) -> str:
    """Read the name Temporal attaches dynamically to an activity definition."""
    return getattr(activity_def, "__temporal_activity_definition").name


@activity.defn(
    name=_activity_name(resolve_workflow_concurrency_limits_enabled_activity)
)
async def _disable_workflow_concurrency_limits() -> bool:
    """Keep the replay history focused on the terminal failure boundary."""
    return False


@activity.defn(name=_activity_name(DSLActivities.prepare_subflow_activity))
async def _legacy_prepare_subflow_activity(
    _input: PrepareSubflowActivityInput,
) -> None:
    """Reproduce the unclassified activity failure emitted before ENG-1407."""
    raise RuntimeError("synthetic legacy subflow preparation failure")


def _raise_legacy_workflow_application_error(
    task_exceptions: Mapping[str, TaskExceptionInfo],
) -> Never:
    """Reproduce the terminal ApplicationError emitted before ENG-1407."""
    n_exceptions = len(task_exceptions)
    formatted_exceptions = "\n".join(
        f"{'=' * 10} ({i + 1}/{n_exceptions}) {details.expr_context}.{ref} {'=' * 10}\n\n{info.exception!s}"
        for i, (ref, info) in enumerate(task_exceptions.items())
        if (details := info.details)
    )
    raise ApplicationError(
        f"Workflow failed with {n_exceptions} error(s)\n\n{formatted_exceptions}",
        {ref: info.details for ref, info in task_exceptions.items()},
        non_retryable=True,
        type=ApplicationError.__name__,
    ) from None


def _ignore_terminal_error_owner(_error: ApplicationError) -> None:
    """Reproduce the absence of terminal owner attribution before ENG-1407."""


async def _recorded_patch_ids(
    temporal_client: Client,
    history: WorkflowHistory,
) -> set[str]:
    """Decode patch IDs recorded in a workflow history."""
    patch_ids: set[str] = set()
    for event in history.events:
        if not event.HasField("marker_recorded_event_attributes"):
            continue
        attributes = event.marker_recorded_event_attributes
        if attributes.marker_name != "core_patch":
            continue
        patch_payload = attributes.details.get("patch-data")
        if patch_payload is None:
            continue
        patch_data = await temporal_client.data_converter.decode(patch_payload.payloads)
        for data in patch_data:
            if isinstance(data, Mapping) and isinstance(data.get("id"), str):
                patch_ids.add(data["id"])
    return patch_ids


@pytest.mark.anyio
async def test_dsl_workflow_replays_legacy_subflow_failure_history(
    temporal_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay a marker-free, pre-classification activity failure history."""
    task_queue = f"dsl-workflow-replay-{uuid.uuid4()}"
    wf_id = WorkflowUUID.new_uuid4()
    run_args = DSLRunArgs(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        wf_id=wf_id,
        dsl=DSLInput(
            title="Legacy replay probe",
            description="Capture an unclassified subflow preparation failure",
            entrypoint=DSLEntrypoint(ref="call_child"),
            actions=[
                ActionStatement(
                    ref="call_child",
                    action="core.workflow.execute",
                    args={"workflow_alias": "missing-child"},
                )
            ],
        ),
        trigger_inputs=InlineObject(data={"source": "replay-test"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={},
        ),
        time_anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )

    current_patched = workflow_module.workflow.patched

    def legacy_patched(patch_id: str) -> bool:
        """Keep the captured history older than the owner-timing patch."""
        if patch_id == ERROR_OWNER_AFTER_HANDLER_PATCH:
            return False
        return current_patched(patch_id)

    with monkeypatch.context() as legacy_worker:
        legacy_worker.setattr(workflow_module.workflow, "patched", legacy_patched)
        legacy_worker.setattr(
            workflow_module,
            "_raise_workflow_application_error",
            _raise_legacy_workflow_application_error,
        )
        legacy_worker.setattr(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
            staticmethod(_ignore_terminal_error_owner),
        )
        async with Worker(
            client=temporal_client,
            task_queue=task_queue,
            activities=[
                _disable_workflow_concurrency_limits,
                _legacy_prepare_subflow_activity,
                WorkflowsManagementService.get_error_handler_workflow_id,
            ],
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
        ):
            handle = await temporal_client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(wf_id),
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=30),
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()

    history = await handle.fetch_history()
    assert extract_error_envelope(exc_info.value) is None
    recorded_patch_ids = await _recorded_patch_ids(temporal_client, history)
    assert ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH not in recorded_patch_ids
    assert ERROR_OWNER_AFTER_HANDLER_PATCH not in recorded_patch_ids

    replay_result = await Replayer(
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        data_converter=temporal_client.data_converter,
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert replay_result.replay_failure is None
