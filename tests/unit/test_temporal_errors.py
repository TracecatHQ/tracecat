from __future__ import annotations

from datetime import timedelta
from inspect import signature

import pytest
from pydantic import TypeAdapter, ValidationError
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tests.shared import capture_application_error as _capture_application_error
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
    TEMPORAL_ERROR_DETAILS_SCHEMA,
    ClassifiedErrorDetail,
    extract_error_envelope,
    extract_error_envelopes,
    parse_classified_detail,
    raise_application_error_from_envelope,
    raise_wrapped_application_error,
    wrap_error,
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
async def test_classified_wrapper_carries_envelope_and_preserves_payload() -> None:
    """The wrapper carries the envelope; its payload survives unwrapping intact."""
    envelope = _user_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    )
    error = _capture_application_error(envelope, wrap_error(envelope, error_info))
    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert len(error.details) == 1
    assert error.type == RuntimeErrorKind.ACTION_EXECUTION_FAILED.value
    assert error.details[0]["schema"] == TEMPORAL_ERROR_DETAILS_SCHEMA
    assert error.details[0]["envelope"]["schema"] == "tracecat.error.v1"
    assert error.details[0]["error"] == error_info.model_dump(mode="json")
    assert extract_error_envelope(error) == envelope
    assert extract_error_envelope(decoded) == envelope
    assert isinstance(decoded, ApplicationError)
    parsed = parse_classified_detail(decoded.details[0])
    assert isinstance(parsed, ClassifiedErrorDetail)
    assert parsed.envelope == envelope
    assert parsed.error == error_info


def test_aggregate_action_error_attribution_lives_on_the_wrapper() -> None:
    """Gather payloads stay envelope-free; the wrapper alone carries attribution."""
    envelope = _platform_envelope()
    child = ActionErrorInfo(
        ref="scatter[0]",
        message=envelope.message,
        type="RuntimeError",
    )
    finalized = FinalizeGatherActivityResult(
        result=InlineObject(data=[]),
        errors=[child],
    )

    serialized_finalized = finalized.model_dump(mode="json")
    parsed_finalized = FinalizeGatherActivityResult.model_validate(serialized_finalized)
    assert "envelope" not in serialized_finalized["errors"][0]
    assert parsed_finalized.errors == [child]

    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        children=parsed_finalized.errors,
    )
    serialized_aggregate = aggregate.model_dump(mode="json")
    assert "envelope" not in serialized_aggregate
    assert "envelope" not in serialized_aggregate["children"][0]

    error = ApplicationError(
        "Gather failed",
        {"gather": wrap_error(envelope, aggregate).model_dump(mode="json")},
    )
    parsed = parse_classified_detail(error.details[0])

    assert extract_error_envelopes(error) == (envelope,)
    assert isinstance(parsed, dict)
    assert parsed["gather"].error == aggregate


@pytest.mark.parametrize("mapped", [False, True])
def test_embedded_payload_envelopes_never_classify(mapped: bool) -> None:
    """Envelope keys inside a payload fail validation, so the detail is unclassified."""
    envelope = _user_envelope()
    serialized_envelope = envelope.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionErrorInfo.model_validate(
            {
                "ref": "scatter[0]",
                "message": envelope.message,
                "type": "ValueError",
                "envelope": serialized_envelope,
            }
        )

    aggregate = {
        "ref": "gather",
        "message": "Gather failed",
        "type": "ApplicationError",
        "envelope": serialized_envelope,
        "children": [
            {
                "ref": "scatter[0]",
                "message": envelope.message,
                "type": "ValueError",
                "envelope": serialized_envelope,
            },
            {
                "ref": "scatter[1]",
                "message": "Legacy failure",
                "type": "RuntimeError",
            },
        ],
    }
    detail = {"gather": aggregate} if mapped else aggregate

    assert extract_error_envelopes(ApplicationError("Gather failed", detail)) == ()


@pytest.mark.anyio
async def test_unclassified_aggregate_keeps_fallback_envelope_after_serialization() -> (
    None
):
    """An unclassified aggregate detail leaves the appended fallback authoritative."""
    fallback = _platform_envelope()
    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        children=[
            ActionErrorInfo(
                ref="scatter[0]",
                message="The action failed",
                type="ValueError",
            ),
            ActionErrorInfo(
                ref="scatter[1]",
                message="Legacy failure",
                type="RuntimeError",
            ),
        ],
    ).model_dump(mode="json")

    error = _capture_application_error(fallback, aggregate)
    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert error.type == fallback.kind.value
    assert error.non_retryable is False
    assert error.details[0] == aggregate
    assert extract_error_envelopes(error) == (fallback,)
    assert extract_error_envelopes(decoded) == (fallback,)


def test_error_handler_input_carries_unwrapped_action_errors() -> None:
    """Handler input carries envelope-free payloads identical to the unwrapped ones."""
    envelope = _platform_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message=envelope.message,
        type="RuntimeError",
    )
    wrapped = parse_classified_detail(
        wrap_error(envelope, error_info).model_dump(mode="json")
    )
    assert isinstance(wrapped, ClassifiedErrorDetail)
    assert isinstance(wrapped.error, ActionErrorInfo)
    assert wrapped.envelope == envelope
    assert wrapped.error == error_info

    handler_wf_id = WorkflowUUID.new_uuid4()
    orig_wf_id = WorkflowUUID.new_uuid4()
    handler_input = ErrorHandlerWorkflowInput(
        message="Workflow failed",
        handler_wf_id=handler_wf_id,
        orig_wf_id=orig_wf_id,
        orig_wf_exec_id=generate_exec_id(orig_wf_id),
        orig_wf_title="Synthetic workflow",
        trigger_type=TriggerType.MANUAL,
        errors=[wrapped.error],
    )

    serialized = TypeAdapter(ErrorHandlerWorkflowInput).dump_python(
        handler_input,
        mode="json",
    )

    assert serialized["errors"][0] == error_info.model_dump(mode="json")
    assert "envelope" not in serialized["errors"][0]


def test_legacy_action_error_is_transported_verbatim_beside_the_wrapper() -> None:
    """A legacy payload travels unchanged; classification rides an appended wrapper."""
    envelope = _user_envelope()
    legacy = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    ).model_dump(mode="json")

    error = _capture_application_error(envelope, legacy)

    assert len(error.details) == 2
    assert error.details[0] == legacy
    assert error.details[1] == wrap_error(envelope).model_dump(mode="json")
    assert error.details[1]["error"] is None
    assert extract_error_envelope(error) == envelope


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


def test_wrapper_map_extracts_every_classified_envelope() -> None:
    """A terminal ``{ref: wrapper}`` map yields each wrapper's envelope in order."""
    user_envelope = _user_envelope()
    platform_envelope = _platform_envelope()
    details = {
        "user_action": wrap_error(
            user_envelope,
            ActionErrorInfo(
                ref="user_action",
                message=user_envelope.message,
                type="ValueError",
            ),
        ).model_dump(mode="json"),
        "platform_action": wrap_error(
            platform_envelope,
            ActionErrorInfo(
                ref="platform_action",
                message=platform_envelope.message,
                type="RuntimeError",
            ),
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


@pytest.mark.parametrize("companion", ["arbitrary", "legacy"])
def test_map_with_any_unwrapped_value_is_unclassified(companion: str) -> None:
    """Classification is all-or-nothing on the map: one unwrapped value voids it."""
    envelope = _user_envelope()
    legacy_payload = ActionErrorInfo(
        ref="legacy_action",
        message="Legacy failure",
        type="RuntimeError",
    ).model_dump(mode="json")
    companion_payload = (
        legacy_payload
        if companion == "legacy"
        else {"payload": "not a classified detail"}
    )
    error = ApplicationError(
        "Mixed payload",
        {
            "action": wrap_error(
                envelope,
                ActionErrorInfo(
                    ref="action",
                    message=envelope.message,
                    type="ValueError",
                ),
            ).model_dump(mode="json"),
            "companion": companion_payload,
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
