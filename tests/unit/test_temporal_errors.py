from __future__ import annotations

from datetime import timedelta
from inspect import signature

import pytest
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tracecat.dsl.action import FinalizeGatherActivityResult
from tracecat.dsl.types import (
    ActionErrorInfo,
    ActionErrorInfoAdapter,
    ClassifiedActionErrorInfo,
)
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
    TracecatRuntimeError,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    application_error_from_envelope,
    extract_error_envelope,
    wrap_application_error,
)


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


@pytest.mark.anyio
async def test_legacy_action_error_payload_is_unchanged_without_envelope() -> None:
    error_info = ActionErrorInfo(ref="action", message="Failed", type="ValueError")

    serialized = ActionErrorInfoAdapter.dump_python(error_info, mode="json")
    failure = Failure()
    await DataConverter.default.encode_failure(
        ApplicationError("Failed", error_info), failure
    )
    decoded = await DataConverter.default.decode_failure(failure)

    assert "envelope" not in serialized
    assert isinstance(decoded, ApplicationError)
    assert "envelope" not in decoded.details[0]


@pytest.mark.anyio
async def test_action_error_payload_carries_discriminated_envelope() -> None:
    envelope = _user_envelope()
    error_info = ClassifiedActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
        envelope=envelope,
    )
    error = application_error_from_envelope(envelope, error_info)
    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert len(error.details) == 1
    assert error.type == RuntimeErrorKind.ACTION_EXECUTION_FAILED.value
    assert error.details[0]["envelope"]["schema"] == "tracecat.error.v1"
    assert extract_error_envelope(error) == envelope
    assert extract_error_envelope(decoded) == envelope
    assert isinstance(decoded, ApplicationError)
    parsed = ActionErrorInfoAdapter.validate_python(decoded.details[0])
    assert isinstance(parsed, ClassifiedActionErrorInfo)
    assert parsed.envelope == envelope


def test_aggregate_action_errors_preserve_classified_children() -> None:
    envelope = _platform_envelope()
    child = ClassifiedActionErrorInfo(
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
    assert isinstance(parsed_finalized.errors[0], ClassifiedActionErrorInfo)

    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        children=parsed_finalized.errors,
    )
    serialized_aggregate = ActionErrorInfoAdapter.dump_python(aggregate, mode="json")
    parsed_aggregate = ActionErrorInfoAdapter.validate_python(serialized_aggregate)

    assert serialized_aggregate["children"][0]["envelope"] == envelope.model_dump(
        mode="json"
    )
    assert parsed_aggregate.children is not None
    assert isinstance(parsed_aggregate.children[0], ClassifiedActionErrorInfo)
    assert parsed_aggregate.children[0].envelope == envelope


def test_legacy_action_error_is_extended_without_changing_existing_fields() -> None:
    envelope = _user_envelope()
    error_info = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    )
    legacy = ActionErrorInfoAdapter.dump_python(error_info, mode="json")

    error = application_error_from_envelope(envelope, error_info)

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

    error = application_error_from_envelope(envelope)

    assert error.non_retryable is expected_non_retryable
    assert "non_retryable" not in signature(application_error_from_envelope).parameters
    assert "error_type" not in signature(application_error_from_envelope).parameters
    assert error.type == envelope.kind.value


def test_non_retryable_envelope_rejects_next_retry_delay() -> None:
    with pytest.raises(ValueError, match="non-retryable"):
        application_error_from_envelope(
            _user_envelope(RetryDisposition.NON_RETRYABLE),
            next_retry_delay=timedelta(seconds=1),
        )


def test_non_action_adapter_preserves_legacy_details_in_order() -> None:
    envelope = _platform_envelope()
    legacy_details = ({"existing": "payload"}, ["second payload"])

    error = application_error_from_envelope(envelope, *legacy_details)

    assert error.details[:2] == legacy_details
    assert len(error.details) == 3
    assert error.type == envelope.kind.value
    assert extract_error_envelope(error) == envelope


@pytest.mark.anyio
async def test_envelope_survives_temporal_failure_serialization() -> None:
    envelope = _platform_envelope()
    error = application_error_from_envelope(envelope, {"existing": "payload"})
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
    original = application_error_from_envelope(envelope)
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
    error = wrap_application_error(sensitive, fallback=envelope)
    failure = Failure()

    await DataConverter.default.encode_failure(error, failure)

    serialized_failure = str(failure)
    assert "secret" not in serialized_failure
    assert "example.invalid" not in serialized_failure
    assert "RuntimeError" in serialized_failure


@pytest.mark.parametrize(
    "detail",
    [
        {"legacy": "payload"},
        {"schema": "tracecat.error.v1"},
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


def test_wrapping_preserves_existing_classification() -> None:
    original_envelope = _user_envelope()
    fallback = _platform_envelope()
    original = application_error_from_envelope(original_envelope, {"legacy": "payload"})

    wrapped = wrap_application_error(original, fallback=fallback)

    assert wrapped.type == original_envelope.kind.value
    assert wrapped.details == original.details
    assert extract_error_envelope(wrapped) == original_envelope
    assert extract_error_envelope(wrapped) != fallback


def test_existing_detail_classification_is_authoritative() -> None:
    original_envelope = _user_envelope()
    fallback = _platform_envelope()
    detail = {
        "envelope": original_envelope.model_dump(mode="json"),
    }

    error = application_error_from_envelope(fallback, detail)

    assert error.message == original_envelope.message
    assert error.non_retryable is True
    assert error.type == original_envelope.kind.value
    assert error.details == (detail,)
    assert extract_error_envelope(error) == original_envelope


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
