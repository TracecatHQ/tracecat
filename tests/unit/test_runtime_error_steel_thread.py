from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl.action import DSLActivities, PrepareSubflowActivityInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement, ExecutionContext
from tracecat.dsl.types import (
    ActionErrorInfo,
    ActionErrorInfoAdapter,
    ClassifiedActionErrorInfo,
    TaskExceptionInfo,
)
from tracecat.dsl.workflow import (
    ERROR_OWNER_SEARCH_ATTRIBUTE_PATCH,
    DSLWorkflow,
    _workflow_application_error,
)
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import (
    application_error_from_envelope,
    extract_error_envelope,
    extract_error_envelopes,
)
from tracecat.temporal.exceptions import UserError
from tracecat.workflow.executions.enums import TemporalSearchAttr


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


def _classified_error_info(
    envelope: ErrorEnvelope, *, ref: str
) -> ClassifiedActionErrorInfo:
    return ClassifiedActionErrorInfo(
        ref=ref,
        message=envelope.message,
        type=envelope.cause_type or "ApplicationError",
        envelope=envelope,
    )


@pytest.mark.anyio
async def test_prepare_subflow_platform_failure_is_classified_and_history_safe() -> (
    None
):
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=sensitive),
        ),
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

    detail = ActionErrorInfoAdapter.validate_python(error.details[0])
    assert isinstance(detail, ClassifiedActionErrorInfo)
    assert detail.envelope == envelope

    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    serialized_failure = str(failure)
    assert "secret" not in serialized_failure
    assert "example.invalid" not in serialized_failure


@pytest.mark.anyio
async def test_prepare_subflow_user_failure_keeps_user_semantics() -> None:
    user_error = UserError("Workflow alias 'child' was not found")

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
    assert envelope.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND
    assert envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert envelope.message == user_error.message
    assert error.non_retryable is True
    assert error.type == envelope.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_keeps_existing_classification_authoritative() -> None:
    original_envelope = ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = application_error_from_envelope(original_envelope)

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
    user_detail = _classified_error_info(user_envelope, ref="user_action")
    platform_detail = _classified_error_info(platform_envelope, ref="platform_action")
    task_exceptions = {
        "user_action": TaskExceptionInfo(
            exception=application_error_from_envelope(user_envelope, user_detail),
            details=user_detail,
        ),
        "platform_action": TaskExceptionInfo(
            exception=application_error_from_envelope(
                platform_envelope, platform_detail
            ),
            details=platform_detail,
        ),
    }

    error = _workflow_application_error(task_exceptions)

    assert error.message == user_envelope.message
    assert error.non_retryable is True
    assert extract_error_envelopes(error) == (user_envelope, platform_envelope)
    assert error.details[0]["user_action"]["envelope"]["schema"] == (
        "tracecat.error.v1"
    )
    assert error.details[0]["platform_action"]["envelope"]["schema"] == (
        "tracecat.error.v1"
    )


def test_legacy_workflow_error_shape_is_unchanged() -> None:
    detail = ActionErrorInfo(ref="action", message="Failed", type="ValueError")
    error = _workflow_application_error(
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


def test_terminal_platform_owner_wins_for_alert_attribution() -> None:
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
        "user_action": ActionErrorInfoAdapter.dump_python(
            _classified_error_info(user_envelope, ref="user_action"), mode="json"
        ),
        "platform_action": ActionErrorInfoAdapter.dump_python(
            _classified_error_info(platform_envelope, ref="platform_action"),
            mode="json",
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
    error = application_error_from_envelope(envelope)

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


def test_legacy_terminal_error_does_not_add_patch_marker_or_owner() -> None:
    error = ApplicationError("Legacy failure", non_retryable=True)

    with (
        patch("tracecat.dsl.workflow.workflow.patched") as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_not_called()
    upsert_mock.assert_not_called()
