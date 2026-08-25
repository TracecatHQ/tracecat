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
    extract_error_envelope,
    raise_application_error_from_envelope,
    raise_wrapped_application_error,
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
