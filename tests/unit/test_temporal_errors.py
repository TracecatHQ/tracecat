from __future__ import annotations

from datetime import timedelta
from inspect import signature

import pytest
from pydantic import TypeAdapter
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.action import FinalizeGatherActivityResult
from tracecat.dsl.types import ActionErrorInfo
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
    TracecatRuntimeError,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    extract_error_envelope,
    extract_error_envelopes,
    raise_application_error_from_envelope,
    raise_wrapped_application_error,
)
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.types import ErrorHandlerWorkflowInput


def _user_envelope(
    retry_disposition: RetryDisposition = RetryDisposition.NON_RETRYABLE,
) -> ErrorEnvelope:
    return ErrorEnvelope.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=retry_disposition,
    )


def _platform_envelope() -> ErrorEnvelope:
    return ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the workflow",
        retry_disposition=RetryDisposition.RETRYABLE,
    )


def _capture_application_error(
    envelope: ErrorEnvelope,
    *details: object,
    next_retry_delay: timedelta | None = None,
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        raise_application_error_from_envelope(
            envelope,
            *details,
            next_retry_delay=next_retry_delay,
        )
    return exc_info.value


def _capture_wrapped_application_error(
    error: BaseException,
    *,
    fallback: ErrorEnvelope,
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        raise_wrapped_application_error(error, fallback=fallback)
    return exc_info.value


@pytest.mark.anyio
async def test_legacy_action_error_payload_is_unchanged_without_envelope() -> None:
    error_info = ActionErrorInfo(ref="action", message="Failed", type="ValueError")

    serialized = error_info.model_dump(mode="json")
    failure = Failure()
    data_converter = get_data_converter()
    await data_converter.encode_failure(ApplicationError("Failed", error_info), failure)
    decoded = await data_converter.decode_failure(failure)

    assert "envelope" not in serialized
    assert isinstance(decoded, ApplicationError)
    assert decoded.details[0] == serialized


@pytest.mark.anyio
async def test_action_error_payload_carries_discriminated_envelope() -> None:
    envelope = _user_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
        envelope=envelope,
    )
    error = _capture_application_error(envelope, error_info)
    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert len(error.details) == 1
    assert error.type == RuntimeErrorKind.ACTION_EXECUTION_FAILED.value
    assert error.details[0]["envelope"]["schema"] == "tracecat.error.v1"
    assert extract_error_envelope(error) == envelope
    assert extract_error_envelope(decoded) == envelope
    assert isinstance(decoded, ApplicationError)
    parsed = ActionErrorInfo.model_validate(decoded.details[0])
    assert parsed.envelope == envelope


def test_aggregate_action_errors_preserve_classified_children() -> None:
    envelope = _platform_envelope()
    child = ActionErrorInfo(
        ref="scatter[0]",
        message=envelope.message,
        type="RuntimeError",
        envelope=envelope,
    )
    finalized = FinalizeGatherActivityResult(
        result=InlineObject(data=[]),
        errors=[child],
    )

    serialized_finalized = finalized.model_dump(mode="json")
    parsed_finalized = FinalizeGatherActivityResult.model_validate(serialized_finalized)
    assert serialized_finalized["errors"][0]["envelope"] == envelope.model_dump(
        mode="json"
    )
    assert parsed_finalized.errors[0].envelope == envelope

    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        children=parsed_finalized.errors,
    )
    serialized_aggregate = aggregate.model_dump(mode="json")
    parsed_aggregate = ActionErrorInfo.model_validate(serialized_aggregate)

    assert serialized_aggregate["children"][0]["envelope"] == envelope.model_dump(
        mode="json"
    )
    assert parsed_aggregate.children is not None
    assert parsed_aggregate.children[0].envelope == envelope

    error = ApplicationError("Gather failed", {"gather": serialized_aggregate})
    assert extract_error_envelopes(error) == (envelope,)


@pytest.mark.parametrize("serialized", [False, True])
def test_aggregate_action_error_rejects_partially_classified_children(
    serialized: bool,
) -> None:
    envelope = _user_envelope()
    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        envelope=envelope,
        children=[
            ActionErrorInfo(
                ref="scatter[0]",
                message=envelope.message,
                type="ValueError",
                envelope=envelope,
            ),
            ActionErrorInfo(
                ref="scatter[1]",
                message="Legacy failure",
                type="RuntimeError",
            ),
        ],
    )
    detail = aggregate.model_dump(mode="json") if serialized else aggregate

    assert extract_error_envelopes(ApplicationError("Gather failed", detail)) == ()


def test_error_handler_input_preserves_action_error_envelope() -> None:
    envelope = _platform_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message=envelope.message,
        type="RuntimeError",
        envelope=envelope,
    )
    handler_wf_id = WorkflowUUID.new_uuid4()
    orig_wf_id = WorkflowUUID.new_uuid4()
    handler_input = ErrorHandlerWorkflowInput(
        message="Workflow failed",
        handler_wf_id=handler_wf_id,
        orig_wf_id=orig_wf_id,
        orig_wf_exec_id=generate_exec_id(orig_wf_id),
        orig_wf_title="Synthetic workflow",
        trigger_type=TriggerType.MANUAL,
        errors=[error_info],
    )

    serialized = TypeAdapter(ErrorHandlerWorkflowInput).dump_python(
        handler_input,
        mode="json",
    )

    assert serialized["errors"][0]["envelope"] == envelope.model_dump(mode="json")


def test_legacy_action_error_is_extended_without_changing_existing_fields() -> None:
    envelope = _user_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    )
    legacy = error_info.model_dump(mode="json")

    error = _capture_application_error(envelope, error_info)

    detail = error.details[0]
    assert {key: detail[key] for key in legacy} == legacy
    assert detail["envelope"] == envelope.model_dump(mode="json")


@pytest.mark.parametrize(
    ("retry_disposition", "expected_non_retryable"),
    [
        (RetryDisposition.RETRYABLE, False),
        (RetryDisposition.NON_RETRYABLE, True),
    ],
)
def test_temporal_retryability_is_derived_from_envelope(
    retry_disposition: RetryDisposition, expected_non_retryable: bool
) -> None:
    envelope = _user_envelope(retry_disposition)

    error = _capture_application_error(envelope)

    assert error.non_retryable is expected_non_retryable
    assert (
        "non_retryable"
        not in signature(raise_application_error_from_envelope).parameters
    )
    assert (
        "error_type" not in signature(raise_application_error_from_envelope).parameters
    )
    assert error.type == envelope.kind.value


def test_non_retryable_envelope_rejects_next_retry_delay() -> None:
    with pytest.raises(ValueError, match="non-retryable"):
        raise_application_error_from_envelope(
            _user_envelope(RetryDisposition.NON_RETRYABLE),
            next_retry_delay=timedelta(seconds=1),
        )


def test_non_action_adapter_preserves_legacy_details_in_order() -> None:
    envelope = _platform_envelope()
    legacy_details = ({"existing": "payload"}, ["second payload"])

    error = _capture_application_error(envelope, *legacy_details)

    assert error.details[:2] == legacy_details
    assert len(error.details) == 3
    assert error.type == envelope.kind.value
    assert extract_error_envelope(error) == envelope


@pytest.mark.anyio
async def test_envelope_survives_temporal_failure_serialization() -> None:
    envelope = _platform_envelope()
    error = _capture_application_error(envelope, {"existing": "payload"})
    failure = Failure()

    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert isinstance(decoded, ApplicationError)
    assert decoded.details[0] == {"existing": "payload"}
    assert decoded.non_retryable is False
    assert extract_error_envelope(decoded) == envelope


@pytest.mark.anyio
async def test_envelope_survives_wrapped_temporal_failure_serialization() -> None:
    envelope = _user_envelope()
    original = _capture_application_error(envelope)
    try:
        raise ApplicationError(
            "Outer wrapper",
            {"legacy": "payload"},
            type="OuterError",
            non_retryable=True,
        ) from original
    except ApplicationError as wrapped:
        failure = Failure()
        await DataConverter.default.encode_failure(wrapped, failure)

    decoded = await DataConverter.default.decode_failure(failure)

    assert extract_error_envelope(decoded) == envelope


@pytest.mark.anyio
async def test_platform_diagnostics_do_not_enter_temporal_history() -> None:
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")
    envelope = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        message="A platform dependency is unavailable",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=sensitive,
    )
    try:
        raise sensitive
    except RuntimeError as caught:
        error = _capture_wrapped_application_error(caught, fallback=envelope)
    failure = Failure()

    await DataConverter.default.encode_failure(error, failure)

    serialized_failure = str(failure)
    assert not failure.HasField("cause")
    assert "secret" not in serialized_failure
    assert "example.invalid" not in serialized_failure
    assert "RuntimeError" in serialized_failure


@pytest.mark.parametrize(
    "detail",
    [
        {"legacy": "payload"},
        {"schema": "tracecat.error.v1"},
        _user_envelope().model_dump(mode="json"),
        {"envelope": {"schema": "not-tracecat.error.v1"}},
        {
            "envelope": {
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "Missing discriminator",
                "retry_disposition": "non_retryable",
                "cause_type": None,
            }
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "envelope": {"schema": "tracecat.error.v1"},
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "envelope": {
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "Missing discriminator",
                "retry_disposition": "non_retryable",
                "cause_type": None,
            },
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "envelope": _user_envelope().model_dump(mode="json"),
            "unexpected": True,
        },
    ],
)
def test_legacy_and_malformed_details_are_not_classified(detail: object) -> None:
    error = ApplicationError("Legacy error", detail)

    assert extract_error_envelope(error) is None


def test_payload_key_does_not_collide_without_valid_discriminator() -> None:
    error = ApplicationError(
        "User payload",
        {
            "ref": "envelope",
            "message": "User-authored payload",
            "type": "Example",
            "envelope": {"schema": "customer.payload.v1"},
        },
    )

    assert extract_error_envelope(error) is None


def test_action_error_detail_requires_nested_schema_discriminator() -> None:
    error = ApplicationError(
        "Legacy error",
        {
            "ref": "action",
            "message": "Missing discriminator",
            "type": "ValueError",
            "envelope": {
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "Missing discriminator",
                "retry_disposition": "non_retryable",
                "cause_type": None,
            },
        },
    )

    assert extract_error_envelope(error) is None


def test_aggregate_action_error_requires_child_schema_discriminator() -> None:
    child = {
        "ref": "scatter[0]",
        "message": "Missing discriminator",
        "type": "ValueError",
        "envelope": {
            "owner": "user",
            "kind": "action.execution.failed",
            "message": "Missing discriminator",
            "retry_disposition": "non_retryable",
            "cause_type": None,
        },
    }
    aggregate = {
        "ref": "gather",
        "message": "Gather failed",
        "type": "ApplicationError",
        "children": [child],
    }

    assert extract_error_envelope(ApplicationError("Gather failed", aggregate)) is None
    assert (
        extract_error_envelope(ApplicationError("Gather failed", {"gather": aggregate}))
        is None
    )


def test_action_error_map_extracts_every_classified_envelope() -> None:
    user_envelope = _user_envelope()
    platform_envelope = _platform_envelope()
    details = {
        "user_action": ActionErrorInfo(
            ref="user_action",
            message=user_envelope.message,
            type="ValueError",
            envelope=user_envelope,
        ).model_dump(mode="json"),
        "platform_action": ActionErrorInfo(
            ref="platform_action",
            message=platform_envelope.message,
            type="RuntimeError",
            envelope=platform_envelope,
        ).model_dump(mode="json"),
    }
    error = ApplicationError("Workflow failed", details)

    assert extract_error_envelopes(error) == (user_envelope, platform_envelope)


def test_arbitrary_nested_envelope_does_not_collide_with_action_error_map() -> None:
    envelope = _user_envelope()
    error = ApplicationError(
        "User payload",
        {
            "arbitrary": {
                "payload": "not an action error",
                "envelope": envelope.model_dump(mode="json"),
            }
        },
    )

    assert extract_error_envelopes(error) == ()


def test_action_error_map_rejects_partial_shape_match() -> None:
    envelope = _user_envelope()
    error = ApplicationError(
        "Mixed payload",
        {
            "action": ActionErrorInfo(
                ref="action",
                message=envelope.message,
                type="ValueError",
                envelope=envelope,
            ).model_dump(mode="json"),
            "arbitrary": {"payload": "not an action error"},
        },
    )

    assert extract_error_envelopes(error) == ()


def test_wrapping_preserves_existing_classification() -> None:
    original_envelope = _user_envelope()
    fallback = _platform_envelope()
    original = _capture_application_error(
        original_envelope,
        {"legacy": "payload"},
    )

    wrapped = _capture_wrapped_application_error(original, fallback=fallback)

    assert wrapped.type == original_envelope.kind.value
    assert wrapped.details == original.details
    assert extract_error_envelope(wrapped) == original_envelope
    assert extract_error_envelope(wrapped) != fallback


@pytest.mark.anyio
async def test_wrapping_drops_outer_details_for_cause_classification() -> None:
    original_envelope = _user_envelope()
    fallback = _platform_envelope()
    classified = _capture_application_error(original_envelope)
    try:
        raise ApplicationError(
            "Outer platform failure",
            {"diagnostic": "postgresql://user:secret@example.invalid/database"},
        ) from classified
    except ApplicationError as outer:
        wrapped = _capture_wrapped_application_error(outer, fallback=fallback)
    failure = Failure()

    await DataConverter.default.encode_failure(wrapped, failure)

    assert len(wrapped.details) == 1
    assert wrapped.details[0]["schema"] == "tracecat.temporal_error.v1"
    assert wrapped.type == original_envelope.kind.value
    assert extract_error_envelope(wrapped) == original_envelope
    assert "secret" not in str(failure)
    assert "example.invalid" not in str(failure)


def test_wrapping_unclassified_application_error_drops_original_details() -> None:
    fallback = _platform_envelope()
    original = ApplicationError(
        "Raw platform failure",
        {"diagnostic": "postgresql://user:secret@example.invalid/database"},
    )

    wrapped = _capture_wrapped_application_error(original, fallback=fallback)

    assert len(wrapped.details) == 1
    assert wrapped.details[0]["schema"] == "tracecat.temporal_error.v1"
    assert "secret" not in str(wrapped.details)
    assert "example.invalid" not in str(wrapped.details)
    assert extract_error_envelope(wrapped) == fallback


@pytest.mark.parametrize(
    ("retry_disposition", "expected_delay"),
    [
        (RetryDisposition.RETRYABLE, timedelta(seconds=5)),
        (RetryDisposition.NON_RETRYABLE, None),
    ],
)
def test_wrapping_applies_retry_delay_only_to_retryable_fallback(
    retry_disposition: RetryDisposition,
    expected_delay: timedelta | None,
) -> None:
    original_delay = timedelta(seconds=5)
    fallback = ErrorEnvelope.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the workflow",
        retry_disposition=retry_disposition,
    )
    original = ApplicationError(
        "Unclassified platform failure",
        next_retry_delay=original_delay,
    )

    wrapped = _capture_wrapped_application_error(original, fallback=fallback)

    assert wrapped.next_retry_delay == expected_delay
    assert wrapped.non_retryable is (
        retry_disposition is RetryDisposition.NON_RETRYABLE
    )
    assert extract_error_envelope(wrapped) == fallback


def test_wrapping_clears_retry_delay_for_non_retryable_cause() -> None:
    envelope = _user_envelope(RetryDisposition.NON_RETRYABLE)
    classified = _capture_application_error(envelope)
    try:
        raise ApplicationError(
            "Outer wrapper",
            next_retry_delay=timedelta(seconds=5),
        ) from classified
    except ApplicationError as outer:
        wrapped = _capture_wrapped_application_error(
            outer,
            fallback=_platform_envelope(),
        )

    assert wrapped.next_retry_delay is None
    assert wrapped.non_retryable is True
    assert extract_error_envelope(wrapped) == envelope


def test_ambiguous_nested_envelope_does_not_override_fallback() -> None:
    nested_envelope = _user_envelope()
    fallback = _platform_envelope()
    detail = {
        "envelope": nested_envelope.model_dump(mode="json"),
        "arbitrary": "outer field",
    }

    error = _capture_application_error(fallback, detail)

    assert error.message == fallback.message
    assert error.non_retryable is False
    assert error.type == fallback.kind.value
    assert error.details[0] == detail
    assert error.details[1]["schema"] == "tracecat.temporal_error.v1"
    assert extract_error_envelope(error) == fallback


def test_exception_chain_preserves_runtime_envelope() -> None:
    envelope = _user_envelope()
    classified = TracecatRuntimeError(envelope)

    try:
        raise RuntimeError("Outer wrapper") from classified
    except RuntimeError as error:
        assert extract_error_envelope(error) == envelope


def test_error_owners_remain_attribution_only() -> None:
    assert set(RuntimeErrorOwner) == {
        RuntimeErrorOwner.USER,
        RuntimeErrorOwner.PLATFORM,
    }
