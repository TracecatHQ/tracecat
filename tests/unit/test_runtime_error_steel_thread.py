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
from tracecat.dsl.schemas import ActionStatement, ExecutionContext
from tracecat.dsl.types import (
    ActionErrorInfo,
    TaskExceptionInfo,
)
from tracecat.dsl.workflow import (
    ERROR_OWNER_AFTER_HANDLER_PATCH,
    ERROR_OWNER_CONTROL_FLOW_PATCH,
    ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH,
    DSLWorkflow,
    _raise_workflow_application_error,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import (
    ClassifiedErrorDetail,
    extract_error_envelope,
    extract_error_envelopes,
    parse_classified_detail,
    wrap_error,
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


def _action_error_info(envelope: ErrorEnvelope, *, ref: str) -> ActionErrorInfo:
    """Build the envelope-free payload an envelope's failure would carry."""
    return ActionErrorInfo(
        ref=ref,
        message=envelope.message,
        type=envelope.cause_type or "ApplicationError",
    )


def _wrapped_error_detail(envelope: ErrorEnvelope, *, ref: str) -> dict[str, Any]:
    """Serialize the classified wrapper that transports an envelope and its payload."""
    return wrap_error(envelope, _action_error_info(envelope, ref=ref)).model_dump(
        mode="json"
    )


def _capture_workflow_application_error(
    task_exceptions: dict[str, TaskExceptionInfo],
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        _raise_workflow_application_error(task_exceptions)
    return exc_info.value


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


@pytest.mark.anyio
async def test_prepare_subflow_platform_failure_is_classified_and_history_safe() -> (
    None
):
    """The failure travels as a wrapper whose envelope keeps diagnostics out of history."""
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
    envelope = extract_error_envelope(error)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_PREPARATION_FAILED
    assert envelope.retry_disposition is RetryDisposition.RETRYABLE
    assert envelope.cause_type == "RuntimeError"
    assert error.non_retryable is False
    assert error.type == envelope.kind.value
    assert error.__cause__ is None

    detail = parse_classified_detail(error.details[0])
    assert isinstance(detail, ClassifiedErrorDetail)
    assert detail.envelope == envelope
    assert detail.error == ActionErrorInfo(
        ref="call_child",
        message=envelope.message,
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
    assert log_fields["error_kind"] == envelope.kind.value
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
    envelope = extract_error_envelope(error)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.USER
    assert envelope.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_INPUT_INVALID
    assert envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert envelope.message == user_error.message
    assert error.non_retryable is True
    assert error.type == envelope.kind.value


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

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.USER
    assert envelope.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND


def test_subflow_user_envelope_control_flow_is_replay_gated() -> None:
    """A wholly user-owned classified failure only steers control flow behind the patch."""
    envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND,
        message="The child workflow could not be found",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        envelope,
        _wrapped_error_detail(envelope, ref="call_child"),
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
    original_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_envelope)

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    assert extract_error_envelope(error) == original_envelope
    assert error.non_retryable is False
    assert error.type == original_envelope.kind.value


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
    envelope = extract_error_envelope(error)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert error.non_retryable is True
    assert error.type == envelope.kind.value


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
    envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(envelope)
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
    assert extract_error_envelope(exc_info.value) == envelope
    assert sensitive not in str(exc_info.value.details)
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_clears_retry_delay_for_non_retryable_envelope() -> None:
    envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(envelope)
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
    assert extract_error_envelope(exc_info.value) == envelope


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


def test_workflow_error_preserves_all_action_envelopes() -> None:
    """The terminal map wraps every task; the platform-wins selection stamps the
    outer error's message, type, and retryability regardless of task order."""
    user_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_envelope, ref="user_action")
    platform_detail = _action_error_info(platform_envelope, ref="platform_action")
    task_exceptions = {
        "user_action": TaskExceptionInfo(
            exception=_capture_application_error(
                user_envelope, wrap_error(user_envelope, user_detail)
            ),
            details=user_detail,
        ),
        "platform_action": TaskExceptionInfo(
            exception=_capture_application_error(
                platform_envelope, wrap_error(platform_envelope, platform_detail)
            ),
            details=platform_detail,
        ),
    }

    error = _capture_workflow_application_error(task_exceptions)

    assert error.message == platform_envelope.message
    assert error.type == platform_envelope.kind.value
    assert error.non_retryable is False
    assert extract_error_envelopes(error) == (user_envelope, platform_envelope)
    assert error.details[0]["user_action"]["envelope"] == user_envelope.model_dump(
        mode="json"
    )
    assert error.details[0]["user_action"]["error"] == user_detail.model_dump(
        mode="json"
    )
    assert error.details[0]["platform_action"][
        "envelope"
    ] == platform_envelope.model_dump(mode="json")
    assert error.details[0]["platform_action"]["error"] == platform_detail.model_dump(
        mode="json"
    )


def test_workflow_error_map_platform_entry_survives_terminal_aggregation() -> None:
    """A platform-owned entry in a task's child terminal map wins re-extraction.

    The scheduler selects platform-wins for mixed child maps; the terminal
    aggregation must retain that ownership instead of collapsing to the first
    (user) envelope in transport order.
    """
    user_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the child action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_envelope, ref="user_child")
    platform_detail = _action_error_info(platform_envelope, ref="platform_child")
    child_error = _capture_application_error(
        user_envelope,
        {
            "user_child": wrap_error(user_envelope, user_detail).model_dump(
                mode="json"
            ),
            "platform_child": wrap_error(platform_envelope, platform_detail).model_dump(
                mode="json"
            ),
        },
    )
    aggregate_detail = ActionErrorInfo(
        ref="call_child",
        message=platform_envelope.message,
        type="ApplicationError",
        children=[user_detail, platform_detail],
    )

    error = _capture_workflow_application_error(
        {
            "call_child": TaskExceptionInfo(
                exception=child_error, details=aggregate_detail
            )
        }
    )

    assert error.message == platform_envelope.message
    assert error.type == platform_envelope.kind.value
    assert error.non_retryable is False
    assert extract_error_envelopes(error) == (platform_envelope,)
    assert error.details[0]["call_child"]["envelope"] == platform_envelope.model_dump(
        mode="json"
    )
    assert error.details[0]["call_child"]["error"] == aggregate_detail.model_dump(
        mode="json"
    )


def test_legacy_workflow_error_shape_is_unchanged() -> None:
    detail = ActionErrorInfo(ref="action", message="Failed", type="ValueError")
    error = _capture_workflow_application_error(
        {
            "action": TaskExceptionInfo(
                exception=ApplicationError("Failed", non_retryable=True),
                details=detail,
            )
        }
    )

    assert error.non_retryable is True
    assert error.details == ({"action": detail},)
    assert extract_error_envelope(error) is None


def test_mixed_workflow_errors_use_the_unclassified_terminal_raise() -> None:
    """One unclassified task keeps the whole terminal raise on the legacy shape."""
    user_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    user_detail = _action_error_info(user_envelope, ref="user_action")
    legacy_detail = ActionErrorInfo(
        ref="legacy_action",
        message="Legacy failure",
        type="RuntimeError",
    )
    error = _capture_workflow_application_error(
        {
            "user_action": TaskExceptionInfo(
                exception=_capture_application_error(
                    user_envelope, wrap_error(user_envelope, user_detail)
                ),
                details=user_detail,
            ),
            "legacy_action": TaskExceptionInfo(
                exception=ApplicationError("Legacy failure", non_retryable=True),
                details=legacy_detail,
            ),
        }
    )

    assert error.message.startswith("Workflow failed with 2 error(s)")
    assert error.details == (
        {"user_action": user_detail, "legacy_action": legacy_detail},
    )
    assert extract_error_envelopes(error) == ()
    with patch("tracecat.dsl.workflow.workflow.patched") as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is False
    patched_mock.assert_not_called()


@pytest.mark.anyio
async def test_error_handler_runs_before_original_owner_is_stamped() -> None:
    """The handler completes before the original error's owner is stamped."""
    instance, args = _error_handler_workflow()
    envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        envelope,
        {"action": _wrapped_error_detail(envelope, ref="action")},
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
    original_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    handler_envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not run the error handler",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_envelope,
        {"action": _wrapped_error_detail(original_envelope, ref="action")},
    )
    handler_error = _capture_application_error(handler_envelope)

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
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
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
            original_error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is handler_error
    upsert_mock.assert_called_once_with(handler_error)


@pytest.mark.anyio
async def test_error_handler_lookup_failure_stamps_escaping_error() -> None:
    instance, args = _error_handler_workflow()
    original_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    lookup_envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not resolve the error handler",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_envelope)
    lookup_error = _capture_application_error(lookup_envelope)

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
    original_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_envelope,
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
    """One platform-owned wrapper in the terminal map stamps the workflow as platform."""
    user_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    details = {
        "user_action": _wrapped_error_detail(user_envelope, ref="user_action"),
        "platform_action": _wrapped_error_detail(
            platform_envelope, ref="platform_action"
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
    envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(envelope)

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
