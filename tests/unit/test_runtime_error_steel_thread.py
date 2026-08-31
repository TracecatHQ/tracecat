from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tests.shared import capture_application_error as _capture_application_error
from tracecat.auth.types import Role
from tracecat.dsl.action import (
    DSLActivities,
    PrepareSubflowActivityInput,
    SubflowDefinitionNotFoundError,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.error_transport import (
    ActionErrorTransportDetail,
    parse_classified_action_error_payload,
)
from tracecat.dsl.scheduler import _classified_action_error_info
from tracecat.dsl.schemas import ROOT_STREAM, ActionStatement, ExecutionContext
from tracecat.dsl.types import (
    ActionErrorInfo,
)
from tracecat.dsl.workflow import (
    ERROR_OWNER_AFTER_HANDLER_PATCH,
    ERROR_OWNER_CONTROL_FLOW_PATCH,
    ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH,
    DSLWorkflow,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classification,
)
from tracecat.temporal.exceptions import UserError
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType


def _prepare_subflow_input() -> PrepareSubflowActivityInput:
    return PrepareSubflowActivityInput(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        task=ActionStatement(
            ref="call_child",
            action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
            args={"workflow_alias": "child"},
        ),
        operand=ExecutionContext(ACTIONS={}, TRIGGER=None),
        key="test/subflow",
    )


def _action_error_info(
    classification: RuntimeErrorClassification, *, ref: str
) -> ActionErrorInfo:
    """Build the classification-free payload for a classified failure."""
    return ActionErrorInfo(
        ref=ref,
        message=classification.message,
        type=classification.cause_type or "ApplicationError",
    )


def _error_transport_detail(
    classification: RuntimeErrorClassification, *, ref: str
) -> dict[str, Any]:
    """Serialize the detail transporting a classification and action diagnostics."""
    return build_error_transport_detail(
        classification, _action_error_info(classification, ref=ref)
    ).model_dump(mode="json")


def _error_handler_workflow() -> tuple[DSLWorkflow, DSLRunArgs]:
    instance = object.__new__(DSLWorkflow)
    role = _prepare_subflow_input().role
    dsl = DSLInput(
        title="Error handler source",
        description="Error handler ownership test",
        entrypoint=DSLEntrypoint(ref="noop"),
        actions=[ActionStatement(ref="noop", action="core.noop")],
    )
    args = DSLRunArgs(role=role, dsl=dsl, wf_id=WorkflowUUID.new_uuid4())
    instance.logger = MagicMock()
    instance.dsl = dsl
    instance.wf_exec_id = f"{args.wf_id.short()}/exec_test"
    return instance, args


def test_scheduler_adapts_non_action_classification_to_error_info() -> None:
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        message="Tracecat could not retrieve stored workflow data",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=RuntimeError("storage transport unavailable"),
    )
    error = _capture_application_error(classification)

    adapted = _classified_action_error_info(
        error,
        ref="fetch_data",
        stream_id=ROOT_STREAM,
    )

    assert adapted is not None
    detail, adapted_classification = adapted
    assert detail.ref == "fetch_data"
    assert detail.message == classification.message
    assert detail.type == classification.kind.value
    assert adapted_classification == classification


@pytest.mark.anyio
async def test_prepare_subflow_platform_failure_is_classified_and_history_safe() -> (
    None
):
    """The failure's classification keeps diagnostics out of durable history."""
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=sensitive),
        ),
        patch("tracecat.dsl.action.logger.error") as logger_error_mock,
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_PREPARATION_FAILED
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert classification.cause_type == "RuntimeError"
    assert error.non_retryable is False
    assert error.type == classification.kind.value
    assert error.__cause__ is None

    detail = parse_classified_action_error_payload(error.details[0])
    assert isinstance(detail, ActionErrorTransportDetail)
    assert detail.classification == classification
    assert detail.diagnostic == ActionErrorInfo(
        ref="call_child",
        message=classification.message,
        type="RuntimeError",
    )

    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    serialized_failure = str(failure)
    assert "secret" not in serialized_failure
    assert "example.invalid" not in serialized_failure
    logger_error_mock.assert_called_once()
    log_fields = logger_error_mock.call_args.kwargs
    assert "error" not in log_fields
    assert log_fields["error_type"] == "RuntimeError"
    assert log_fields["error_kind"] == classification.kind.value
    assert "secret" not in str(logger_error_mock.call_args)


@pytest.mark.anyio
async def test_prepare_subflow_input_failure_keeps_user_semantics() -> None:
    user_error = UserError("Invalid child workflow arguments")

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=user_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_INPUT_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.message == user_error.message
    assert error.non_retryable is True
    assert error.type == classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_missing_definition_uses_not_found_kind() -> None:
    user_error = SubflowDefinitionNotFoundError(
        "The child workflow definition could not be found"
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=user_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND


def test_subflow_user_classification_control_flow_is_replay_gated() -> None:
    """A wholly user-owned classified failure only steers control flow behind the patch."""
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND,
        message="The child workflow could not be found",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        classification,
        _error_transport_detail(classification, ref="call_child"),
    )

    with patch(
        "tracecat.dsl.workflow.workflow.patched",
        return_value=False,
    ) as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is False
        patched_mock.assert_called_once_with(ERROR_OWNER_CONTROL_FLOW_PATCH)

    with patch(
        "tracecat.dsl.workflow.workflow.patched",
        return_value=True,
    ) as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is True
        patched_mock.assert_called_once_with(ERROR_OWNER_CONTROL_FLOW_PATCH)


@pytest.mark.anyio
async def test_prepare_subflow_keeps_existing_classification_authoritative() -> None:
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    assert extract_error_classification(error) == original_classification
    assert error.non_retryable is False
    assert error.type == original_classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_keeps_existing_non_retryable_semantics() -> None:
    original_error = ApplicationError(
        "Do not retry this operation",
        type="DependencyRejectedRequest",
        non_retryable=True,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert error.non_retryable is True
    assert error.type == classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_drops_unclassified_application_error_details() -> None:
    sensitive = "postgresql://user:secret@example.invalid/database"
    original_error = ApplicationError(
        "Dependency failed",
        {"diagnostic": sensitive},
        type="DependencyError",
        non_retryable=True,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        patch("tracecat.dsl.action.logger.error"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    failure = Failure()
    await DataConverter.default.encode_failure(exc_info.value, failure)
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_filters_unclassified_sibling_details() -> None:
    sensitive = "SENSITIVE_MARKER"
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(classification)
    original_error = ApplicationError(
        classified.message,
        *classified.details,
        {"diagnostic": sensitive},
        type=classified.type,
        non_retryable=classified.non_retryable,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        patch("tracecat.dsl.action.logger.error"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    failure = Failure()
    await DataConverter.default.encode_failure(exc_info.value, failure)
    assert extract_error_classification(exc_info.value) == classification
    assert sensitive not in str(exc_info.value.details)
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_clears_retry_delay_for_non_retryable_classification() -> (
    None
):
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(classification)
    original_error = ApplicationError(
        classified.message,
        *classified.details,
        type=classified.type,
        non_retryable=True,
        next_retry_delay=timedelta(seconds=5),
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    assert exc_info.value.non_retryable is True
    assert exc_info.value.next_retry_delay is None
    assert extract_error_classification(exc_info.value) == classification


@pytest.mark.anyio
async def test_prepare_subflow_cancellation_keeps_native_semantics() -> None:
    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())


@pytest.mark.anyio
async def test_error_handler_runs_before_original_owner_is_stamped() -> None:
    """The handler completes before the original error's owner is stamped."""
    instance, args = _error_handler_workflow()
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        classification,
        {"action": _error_transport_detail(classification, ref="action")},
    )
    events: list[str] = []

    async def run_handler(_: DSLRunArgs) -> None:
        events.append("handler")

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            instance,
            "_prepare_error_handler_workflow",
            new=AsyncMock(return_value=args),
        ),
        patch.object(
            instance,
            "_run_error_handler_workflow",
            new=AsyncMock(side_effect=run_handler),
        ) as run_handler_mock,
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
            side_effect=lambda _: events.append("upsert"),
        ) as upsert_mock,
        patch("tracecat.dsl.workflow.workflow.info"),
        patch(
            "tracecat.dsl.workflow.get_trigger_type",
            return_value=TriggerType.MANUAL,
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is error
    run_handler_mock.assert_awaited_once_with(args)
    upsert_mock.assert_called_once_with(error)
    assert events == ["handler", "upsert"]


@pytest.mark.anyio
async def test_error_handler_failure_replaces_original_terminal_owner() -> None:
    """A failing handler becomes the terminal error and owns the stamped attribution."""
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    handler_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not run the error handler",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_classification,
        {"action": _error_transport_detail(original_classification, ref="action")},
    )
    handler_error = _capture_application_error(handler_classification)

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            instance,
            "_prepare_error_handler_workflow",
            new=AsyncMock(return_value=args),
        ),
        patch.object(
            instance,
            "_run_error_handler_workflow",
            new=AsyncMock(side_effect=handler_error),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
        patch("tracecat.dsl.workflow.workflow.info"),
        patch(
            "tracecat.dsl.workflow.get_trigger_type",
            return_value=TriggerType.MANUAL,
        ),
    ):
        try:
            raise original_error
        except ApplicationError as active_error:
            with pytest.raises(ApplicationError) as exc_info:
                await instance._handle_application_error(
                    args,
                    active_error,
                    stamp_terminal_owner=True,
                )

    assert exc_info.value is handler_error
    assert handler_error.__context__ is original_error
    patched_mock.assert_called_once_with(ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH)
    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    assert len(updates) == 1
    assert updates[0].key.name == TemporalSearchAttr.ERROR_OWNER.value
    assert updates[0].value == RuntimeErrorOwner.PLATFORM.value


@pytest.mark.anyio
async def test_unclassified_handler_failure_does_not_inherit_original_owner() -> None:
    """Incidental exception context cannot classify an unclassified handler failure."""
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)
    handler_error = RuntimeError("Handler lookup failed")

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(side_effect=handler_error),
        ),
        patch("tracecat.dsl.workflow.workflow.patched") as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        try:
            raise original_error
        except ApplicationError as active_error:
            with pytest.raises(RuntimeError) as exc_info:
                await instance._handle_application_error(
                    args,
                    active_error,
                    stamp_terminal_owner=True,
                )

    assert exc_info.value is handler_error
    assert handler_error.__context__ is original_error
    patched_mock.assert_not_called()
    upsert_mock.assert_not_called()


@pytest.mark.anyio
async def test_error_handler_lookup_failure_stamps_escaping_error() -> None:
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    lookup_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not resolve the error handler",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)
    lookup_error = _capture_application_error(lookup_classification)

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(side_effect=lookup_error),
        ),
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
        ) as upsert_mock,
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            original_error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is lookup_error
    upsert_mock.assert_called_once_with(lookup_error)


@pytest.mark.anyio
async def test_error_handler_detail_adaptation_failure_stamps_escaping_error() -> None:
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_classification,
        {"action": {"invalid": "detail"}},
    )

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
        ) as upsert_mock,
        pytest.raises(ValidationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            original_error,
            stamp_terminal_owner=True,
        )

    upsert_mock.assert_called_once_with(exc_info.value)


def test_terminal_platform_owner_wins_for_alert_attribution() -> None:
    """One platform classification in the terminal map stamps platform ownership."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    details = {
        "user_action": _error_transport_detail(user_classification, ref="user_action"),
        "platform_action": _error_transport_detail(
            platform_classification, ref="platform_action"
        ),
    }
    error = ApplicationError("Workflow failed", details, non_retryable=True)

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_called_once_with(ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH)
    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    assert len(updates) == 1
    assert updates[0].key.name == TemporalSearchAttr.ERROR_OWNER.value
    assert updates[0].value == RuntimeErrorOwner.PLATFORM.value


def test_terminal_owner_upsert_is_replay_gated() -> None:
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(classification)

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=False,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_called_once_with(ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH)
    upsert_mock.assert_not_called()


def test_error_handler_owner_timing_has_distinct_replay_patch() -> None:
    assert ERROR_OWNER_AFTER_HANDLER_PATCH not in {
        ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH,
        ERROR_OWNER_CONTROL_FLOW_PATCH,
    }


def test_legacy_terminal_error_does_not_add_patch_marker_or_owner() -> None:
    error = ApplicationError("Legacy failure", non_retryable=True)

    with (
        patch("tracecat.dsl.workflow.workflow.patched") as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_not_called()
    upsert_mock.assert_not_called()
