"""Replay compatibility tests for DSLWorkflow histories."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Never

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tests.shared import recorded_patch_ids
from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl import workflow as workflow_module
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.action import DSLActivities, PrepareSubflowActivityInput
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.interceptor import RuntimeErrorAttributionInterceptor
from tracecat.dsl.schemas import ActionStatement, RunActionInput
from tracecat.dsl.types import ActionErrorInfo, TaskExceptionInfo
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.storage.object import InlineObject, StoredObject
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classification,
    raise_application_error_from_classification,
)
from tracecat.temporal.patches import WorkflowPatch
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import GetErrorHandlerWorkflowIDActivityInputs

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


@pytest.fixture
async def replay_env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Run replay capture against an isolated real Temporal server."""
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as environment:
        yield environment


@activity.defn(name="execute_action_activity")
async def _classified_action_failure_activity(
    input: RunActionInput,
    _role: Role,
) -> None:
    """Record a stable user-owned action failure in Temporal history."""
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The synthetic action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    raise_application_error_from_classification(
        classification,
        build_error_transport_detail(
            classification,
            ActionErrorInfo(
                ref=input.task.ref,
                message=classification.message,
                type="SyntheticActionError",
            ),
        ),
    )


@activity.defn(
    name=_activity_name(WorkflowsManagementService.get_error_handler_workflow_id)
)
async def _legacy_missing_error_handler(
    _input: GetErrorHandlerWorkflowIDActivityInputs,
) -> None:
    """Reproduce the platform-classified handler lookup failure being replaced."""
    raise_application_error_from_classification(
        RuntimeErrorClassification.platform(
            kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
            message="Tracecat could not resolve the configured error handler",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        )
    )


@workflow.defn(name="DSLWorkflow", sandboxed=False)
class _LegacyHandlerTerminalWorkflow(DSLWorkflow):
    """Capture the former handler-terminal behavior under the production type."""

    @workflow.run
    async def run(self, args: DSLRunArgs) -> StoredObject:
        return await super().run(args)

    async def _handle_application_error(
        self,
        args: DSLRunArgs,
        error: ApplicationError,
        *,
        stamp_terminal_owner: bool,
    ) -> Never:
        try:
            await self._get_error_handler_workflow_id(args)
        except Exception as handler_error:
            if stamp_terminal_owner:
                self._upsert_terminal_error_owner(handler_error)
            raise handler_error from error
        raise AssertionError("The synthetic handler lookup should fail")


def _handler_failure_run_args() -> DSLRunArgs:
    wf_id = WorkflowUUID.new_uuid4()
    return DSLRunArgs(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        wf_id=wf_id,
        dsl=DSLInput(
            title="Error handler replay probe",
            description="Capture error-handler failure behavior",
            entrypoint=DSLEntrypoint(ref="run"),
            actions=[
                ActionStatement(
                    ref="run",
                    action="core.noop",
                    args={},
                )
            ],
            error_handler="synthetic-missing-handler",
        ),
        trigger_inputs=InlineObject(data={"source": "replay-test"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
        time_anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def _record_handler_failure(
    temporal_client: Client,
    workflow_class: type[DSLWorkflow],
) -> tuple[WorkflowHandle[Any, Any], WorkflowFailureError]:
    task_queue = f"dsl-handler-replay-{uuid.uuid4()}"
    run_args = _handler_failure_run_args()
    async with (
        Worker(
            client=temporal_client,
            task_queue=task_queue,
            activities=[
                _disable_workflow_concurrency_limits,
                _legacy_missing_error_handler,
            ],
            workflows=[workflow_class],
            workflow_runner=new_sandbox_runner(),
            interceptors=[RuntimeErrorAttributionInterceptor()],
        ),
        Worker(
            client=temporal_client,
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            activities=[_classified_action_failure_activity],
        ),
    ):
        handle = await temporal_client.start_workflow(
            "DSLWorkflow",
            run_args,
            id=generate_exec_id(run_args.wf_id),
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=30),
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()
    return handle, exc_info.value


@pytest.mark.anyio
async def test_dsl_workflow_classifies_missing_workspace_inside_run(
    temporal_client: Client,
) -> None:
    """Classify missing workspace data in a real sandboxed workflow instance."""
    task_queue = f"dsl-workflow-bootstrap-{uuid.uuid4()}"
    wf_id = WorkflowUUID.new_uuid4()
    run_args = DSLRunArgs(
        role=Role(type="service", service_id="tracecat-runner"),
        wf_id=wf_id,
    )

    async with Worker(
        client=temporal_client,
        task_queue=task_queue,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        interceptors=[RuntimeErrorAttributionInterceptor()],
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

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
    history = await handle.fetch_history()
    patch_ids = await recorded_patch_ids(temporal_client, history)
    assert WorkflowPatch.RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR.value in patch_ids


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
        if patch_id == WorkflowPatch.ERROR_OWNER_AFTER_HANDLER:
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
    assert extract_error_classification(exc_info.value) is None
    patch_ids = await recorded_patch_ids(temporal_client, history)
    assert WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE not in patch_ids
    assert WorkflowPatch.ERROR_OWNER_AFTER_HANDLER not in patch_ids

    replay_result = await Replayer(
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        data_converter=temporal_client.data_converter,
        interceptors=[RuntimeErrorAttributionInterceptor()],
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert replay_result.replay_failure is None


@pytest.mark.anyio
async def test_dsl_workflow_handler_failure_preserves_original_with_one_upsert(
    replay_env: WorkflowEnvironment,
) -> None:
    """New histories keep the causal failure and one terminal owner upsert."""
    temporal_client = replay_env.client
    handle, recorded_failure = await _record_handler_failure(
        temporal_client,
        DSLWorkflow,
    )

    recorded_classification = extract_error_classification(recorded_failure)
    assert recorded_classification is not None
    assert recorded_classification.owner is RuntimeErrorOwner.USER
    assert recorded_classification.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    history = await handle.fetch_history()
    patch_ids = await recorded_patch_ids(temporal_client, history)
    assert WorkflowPatch.PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE in patch_ids
    activity_failure_events = [
        event
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
    ]
    assert len(activity_failure_events) == 2
    handler_failure_event = activity_failure_events[-1]
    terminal_events = [
        event.event_type
        for event in history.events
        if event.event_id > handler_failure_event.event_id
        if (
            event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED
            or (
                event.event_type
                == EventType.EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES
                and TemporalSearchAttr.ERROR_OWNER.value
                in event.upsert_workflow_search_attributes_event_attributes.search_attributes.indexed_fields
            )
        )
    ]
    assert terminal_events == [
        EventType.EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES,
        EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
    ]
    recorded_error = await temporal_client.data_converter.decode_failure(
        history.events[-1].workflow_execution_failed_event_attributes.failure
    )
    terminal_classification = extract_error_classification(recorded_error)
    assert terminal_classification is not None
    assert terminal_classification.owner is RuntimeErrorOwner.USER
    assert terminal_classification.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    assert (await handle.describe()).typed_search_attributes.get(
        TemporalSearchAttr.ERROR_OWNER.key
    ) == RuntimeErrorOwner.USER.value


@pytest.mark.anyio
async def test_dsl_workflow_replays_handler_terminal_replacement_history(
    replay_env: WorkflowEnvironment,
) -> None:
    """The preservation patch replays the exact former terminal command path."""
    temporal_client = replay_env.client
    handle, recorded_failure = await _record_handler_failure(
        temporal_client,
        _LegacyHandlerTerminalWorkflow,
    )

    recorded_classification = extract_error_classification(recorded_failure)
    assert recorded_classification is not None
    assert recorded_classification.owner is RuntimeErrorOwner.PLATFORM
    history = await handle.fetch_history()
    activity_failure_events = [
        event
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
    ]
    assert len(activity_failure_events) == 2
    handler_failure_event = activity_failure_events[-1]
    terminal_events = [
        event.event_type
        for event in history.events
        if event.event_id > handler_failure_event.event_id
        if (
            event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED
            or (
                event.event_type
                == EventType.EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES
                and TemporalSearchAttr.ERROR_OWNER.value
                in event.upsert_workflow_search_attributes_event_attributes.search_attributes.indexed_fields
            )
        )
    ]
    assert terminal_events == [
        EventType.EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES,
        EventType.EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES,
        EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
    ]
    recorded_error = await temporal_client.data_converter.decode_failure(
        history.events[-1].workflow_execution_failed_event_attributes.failure
    )
    terminal_classification = extract_error_classification(recorded_error)
    assert terminal_classification is not None
    assert terminal_classification.owner is RuntimeErrorOwner.PLATFORM
    assert terminal_classification.kind is RuntimeErrorKind.RUNTIME_UNCLASSIFIED
    assert (await handle.describe()).typed_search_attributes.get(
        TemporalSearchAttr.ERROR_OWNER.key
    ) == RuntimeErrorOwner.USER.value

    replay_result = await Replayer(
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        data_converter=temporal_client.data_converter,
        interceptors=[RuntimeErrorAttributionInterceptor()],
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert replay_result.replay_failure is None
