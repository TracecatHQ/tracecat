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
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
    TracecatRuntimeError,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    ERROR_TRANSPORT_DETAIL_SCHEMA,
    ErrorTransportDetail,
    build_error_transport_detail,
    extract_error_classification,
    extract_error_classifications,
    parse_classified_error_payload,
    raise_application_error_from_classification,
    raise_wrapped_application_error,
)
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.types import ErrorHandlerWorkflowInput


def _user_classification(
    retry_disposition: RetryDisposition = RetryDisposition.NON_RETRYABLE,
) -> RuntimeErrorClassification:
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=retry_disposition,
    )


def _platform_classification() -> RuntimeErrorClassification:
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the workflow",
        retry_disposition=RetryDisposition.RETRYABLE,
    )


def _capture_wrapped_application_error(
    error: BaseException,
    *,
    fallback_classification: RuntimeErrorClassification,
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        raise_wrapped_application_error(
            error,
            fallback_classification=fallback_classification,
        )
    return exc_info.value


@pytest.mark.anyio
async def test_legacy_action_error_payload_is_unchanged_without_classification() -> (
    None
):
    error_info = ActionErrorInfo(ref="action", message="Failed", type="ValueError")

    serialized = error_info.model_dump(mode="json")
    failure = Failure()
    data_converter = get_data_converter()
    await data_converter.encode_failure(ApplicationError("Failed", error_info), failure)
    decoded = await data_converter.decode_failure(failure)

    assert "classification" not in serialized
    assert isinstance(decoded, ApplicationError)
    assert decoded.details[0] == serialized


@pytest.mark.anyio
async def test_transport_detail_carries_classification_and_preserves_payload() -> None:
    """The transport detail preserves classification and action diagnostics."""
    classification = _user_classification()
    error_info = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    )
    error = _capture_application_error(
        classification, build_error_transport_detail(classification, error_info)
    )
    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert len(error.details) == 1
    assert error.type == RuntimeErrorKind.ACTION_EXECUTION_FAILED.value
    assert error.details[0]["schema"] == ERROR_TRANSPORT_DETAIL_SCHEMA
    assert error.details[0]["classification"]["schema"] == "tracecat.error.v1"
    assert error.details[0]["action_error"] == error_info.model_dump(mode="json")
    assert extract_error_classification(error) == classification
    assert extract_error_classification(decoded) == classification
    assert isinstance(decoded, ApplicationError)
    parsed = parse_classified_error_payload(decoded.details[0])
    assert isinstance(parsed, ErrorTransportDetail)
    assert parsed.classification == classification
    assert parsed.action_error == error_info


def test_aggregate_action_error_attribution_lives_on_transport_detail() -> None:
    """Gather diagnostics stay classification-free; the transport carries it."""
    classification = _platform_classification()
    child = ActionErrorInfo(
        ref="scatter[0]",
        message=classification.message,
        type="RuntimeError",
    )
    finalized = FinalizeGatherActivityResult(
        result=InlineObject(data=[]),
        errors=[child],
    )

    serialized_finalized = finalized.model_dump(mode="json")
    parsed_finalized = FinalizeGatherActivityResult.model_validate(serialized_finalized)
    assert "classification" not in serialized_finalized["errors"][0]
    assert parsed_finalized.errors == [child]

    aggregate = ActionErrorInfo(
        ref="gather",
        message="Gather failed",
        type="ApplicationError",
        children=parsed_finalized.errors,
    )
    serialized_aggregate = aggregate.model_dump(mode="json")
    assert "classification" not in serialized_aggregate
    assert "classification" not in serialized_aggregate["children"][0]

    error = ApplicationError(
        "Gather failed",
        {
            "gather": build_error_transport_detail(
                classification, aggregate
            ).model_dump(mode="json")
        },
    )
    parsed = parse_classified_error_payload(error.details[0])

    assert extract_error_classifications(error) == (classification,)
    assert isinstance(parsed, dict)
    assert parsed["gather"].action_error == aggregate


@pytest.mark.parametrize("mapped", [False, True])
def test_embedded_payload_classifications_never_classify(mapped: bool) -> None:
    """Classification keys inside a payload fail validation, so the detail is unclassified."""
    classification = _user_classification()
    serialized_classification = classification.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionErrorInfo.model_validate(
            {
                "ref": "scatter[0]",
                "message": classification.message,
                "type": "ValueError",
                "classification": serialized_classification,
            }
        )

    aggregate = {
        "ref": "gather",
        "message": "Gather failed",
        "type": "ApplicationError",
        "classification": serialized_classification,
        "children": [
            {
                "ref": "scatter[0]",
                "message": classification.message,
                "type": "ValueError",
                "classification": serialized_classification,
            },
            {
                "ref": "scatter[1]",
                "message": "Legacy failure",
                "type": "RuntimeError",
            },
        ],
    }
    detail = {"gather": aggregate} if mapped else aggregate

    assert (
        extract_error_classifications(ApplicationError("Gather failed", detail)) == ()
    )


@pytest.mark.anyio
async def test_unclassified_aggregate_keeps_fallback_classification_after_serialization() -> (
    None
):
    """An unclassified aggregate detail leaves the appended fallback authoritative."""
    fallback = _platform_classification()
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
    assert extract_error_classifications(error) == (fallback,)
    assert extract_error_classifications(decoded) == (fallback,)


def test_error_handler_input_carries_unwrapped_action_errors() -> None:
    """Handler input carries classification-free payloads identical to the unwrapped ones."""
    classification = _platform_classification()
    error_info = ActionErrorInfo(
        ref="action",
        message=classification.message,
        type="RuntimeError",
    )
    transport_detail = parse_classified_error_payload(
        build_error_transport_detail(classification, error_info).model_dump(mode="json")
    )
    assert isinstance(transport_detail, ErrorTransportDetail)
    assert isinstance(transport_detail.action_error, ActionErrorInfo)
    assert transport_detail.classification == classification
    assert transport_detail.action_error == error_info

    handler_wf_id = WorkflowUUID.new_uuid4()
    orig_wf_id = WorkflowUUID.new_uuid4()
    handler_input = ErrorHandlerWorkflowInput(
        message="Workflow failed",
        handler_wf_id=handler_wf_id,
        orig_wf_id=orig_wf_id,
        orig_wf_exec_id=generate_exec_id(orig_wf_id),
        orig_wf_title="Synthetic workflow",
        trigger_type=TriggerType.MANUAL,
        errors=[transport_detail.action_error],
    )

    serialized = TypeAdapter(ErrorHandlerWorkflowInput).dump_python(
        handler_input,
        mode="json",
    )

    assert serialized["errors"][0] == error_info.model_dump(mode="json")
    assert "classification" not in serialized["errors"][0]


def test_legacy_action_error_is_transported_beside_classification_detail() -> None:
    """Legacy diagnostics remain unchanged beside the appended classification."""
    classification = _user_classification()
    legacy = ActionErrorInfo(
        ref="action",
        message="The action failed",
        type="ValueError",
    ).model_dump(mode="json")

    error = _capture_application_error(classification, legacy)

    assert len(error.details) == 2
    assert error.details[0] == legacy
    assert error.details[1] == build_error_transport_detail(classification).model_dump(
        mode="json"
    )
    assert error.details[1]["action_error"] is None
    assert extract_error_classification(error) == classification


@pytest.mark.parametrize(
    ("retry_disposition", "expected_non_retryable"),
    [
        (RetryDisposition.RETRYABLE, False),
        (RetryDisposition.NON_RETRYABLE, True),
    ],
)
def test_temporal_retryability_is_derived_from_classification(
    retry_disposition: RetryDisposition, expected_non_retryable: bool
) -> None:
    classification = _user_classification(retry_disposition)

    error = _capture_application_error(classification)

    assert error.non_retryable is expected_non_retryable
    assert (
        "non_retryable"
        not in signature(raise_application_error_from_classification).parameters
    )
    assert (
        "error_type"
        not in signature(raise_application_error_from_classification).parameters
    )
    assert error.type == classification.kind.value


def test_non_retryable_classification_rejects_next_retry_delay() -> None:
    with pytest.raises(ValueError, match="non-retryable"):
        raise_application_error_from_classification(
            _user_classification(RetryDisposition.NON_RETRYABLE),
            next_retry_delay=timedelta(seconds=1),
        )


def test_non_action_adapter_preserves_legacy_details_in_order() -> None:
    classification = _platform_classification()
    legacy_details = ({"existing": "payload"}, ["second payload"])

    error = _capture_application_error(classification, *legacy_details)

    assert error.details[:2] == legacy_details
    assert len(error.details) == 3
    assert error.type == classification.kind.value
    assert extract_error_classification(error) == classification


@pytest.mark.anyio
async def test_classification_survives_temporal_failure_serialization() -> None:
    classification = _platform_classification()
    error = _capture_application_error(classification, {"existing": "payload"})
    failure = Failure()

    await DataConverter.default.encode_failure(error, failure)
    decoded = await DataConverter.default.decode_failure(failure)

    assert isinstance(decoded, ApplicationError)
    assert decoded.details[0] == {"existing": "payload"}
    assert decoded.non_retryable is False
    assert extract_error_classification(decoded) == classification


@pytest.mark.anyio
async def test_classification_survives_wrapped_temporal_failure_serialization() -> None:
    classification = _user_classification()
    original = _capture_application_error(classification)
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

    assert extract_error_classification(decoded) == classification


@pytest.mark.anyio
async def test_platform_diagnostics_do_not_enter_temporal_history() -> None:
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        message="A platform dependency is unavailable",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=sensitive,
    )
    try:
        raise sensitive
    except RuntimeError as caught:
        error = _capture_wrapped_application_error(
            caught,
            fallback_classification=classification,
        )
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
        _user_classification().model_dump(mode="json"),
        # The superseded transport key remains unclassified.
        {"envelope": {"schema": "not-tracecat.error.v1"}},
        {
            "classification": _user_classification().model_dump(mode="json"),
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "classification": {"schema": "tracecat.error.v1"},
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "classification": {
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "Missing discriminator",
                "retry_disposition": "non_retryable",
                "cause_type": None,
            },
        },
        {
            "schema": "tracecat.temporal_error.v1",
            "classification": _user_classification().model_dump(mode="json"),
            "unexpected": True,
        },
    ],
)
def test_legacy_and_malformed_details_are_not_classified(detail: object) -> None:
    error = ApplicationError("Legacy error", detail)

    assert extract_error_classification(error) is None


def test_payload_key_does_not_collide_without_valid_discriminator() -> None:
    error = ApplicationError(
        "User payload",
        {
            "ref": "classification",
            "message": "User-authored payload",
            "type": "Example",
            "classification": {"schema": "customer.payload.v1"},
        },
    )

    assert extract_error_classification(error) is None


def test_action_error_detail_requires_nested_schema_discriminator() -> None:
    error = ApplicationError(
        "Legacy error",
        {
            "schema": "tracecat.temporal_error.v1",
            "classification": {
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "Missing discriminator",
                "retry_disposition": "non_retryable",
                "cause_type": None,
            },
            "action_error": {
                "ref": "action",
                "message": "Missing discriminator",
                "type": "ValueError",
            },
        },
    )

    assert extract_error_classification(error) is None


def test_aggregate_action_error_requires_child_schema_discriminator() -> None:
    child = {
        "ref": "scatter[0]",
        "message": "Missing discriminator",
        "type": "ValueError",
    }
    aggregate = {
        "ref": "gather",
        "message": "Gather failed",
        "type": "ApplicationError",
        "children": [child],
    }

    malformed_transport = {
        "schema": "tracecat.temporal_error.v1",
        "classification": {
            "owner": "user",
            "kind": "action.execution.failed",
            "message": "Missing discriminator",
            "retry_disposition": "non_retryable",
            "cause_type": None,
        },
        "action_error": aggregate,
    }

    assert (
        extract_error_classification(
            ApplicationError("Gather failed", malformed_transport)
        )
        is None
    )
    assert (
        extract_error_classification(
            ApplicationError("Gather failed", {"gather": malformed_transport})
        )
        is None
    )


def test_transport_map_extracts_every_classification() -> None:
    """A terminal transport map yields each classification in order."""
    user_classification = _user_classification()
    platform_classification = _platform_classification()
    details = {
        "user_action": build_error_transport_detail(
            user_classification,
            ActionErrorInfo(
                ref="user_action",
                message=user_classification.message,
                type="ValueError",
            ),
        ).model_dump(mode="json"),
        "platform_action": build_error_transport_detail(
            platform_classification,
            ActionErrorInfo(
                ref="platform_action",
                message=platform_classification.message,
                type="RuntimeError",
            ),
        ).model_dump(mode="json"),
    }
    error = ApplicationError("Workflow failed", details)

    assert extract_error_classifications(error) == (
        user_classification,
        platform_classification,
    )


def test_classification_extraction_can_ignore_only_implicit_context() -> None:
    """Boundary extraction skips incidental context but keeps deliberate causes."""
    classification = _user_classification()
    classified_error = _capture_application_error(classification)

    try:
        raise classified_error
    except ApplicationError:
        try:
            raise RuntimeError("Handler failed")
        except RuntimeError as implicit_error:
            assert implicit_error.__context__ is classified_error
            assert extract_error_classifications(implicit_error) == (classification,)
            assert (
                extract_error_classifications(
                    implicit_error,
                    include_implicit_context=False,
                )
                == ()
            )

    explicit_error = RuntimeError("Explicit wrapper")
    explicit_error.__cause__ = classified_error
    assert extract_error_classifications(
        explicit_error,
        include_implicit_context=False,
    ) == (classification,)


def test_arbitrary_nested_classification_does_not_collide_with_action_error_map() -> (
    None
):
    classification = _user_classification()
    error = ApplicationError(
        "User payload",
        {
            "arbitrary": {
                "payload": "not an action error",
                "classification": classification.model_dump(mode="json"),
            }
        },
    )

    assert extract_error_classifications(error) == ()


@pytest.mark.parametrize("companion", ["arbitrary", "legacy"])
def test_map_with_any_unwrapped_value_is_unclassified(companion: str) -> None:
    """Classification is all-or-nothing on the map: one unwrapped value voids it."""
    classification = _user_classification()
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
            "action": build_error_transport_detail(
                classification,
                ActionErrorInfo(
                    ref="action",
                    message=classification.message,
                    type="ValueError",
                ),
            ).model_dump(mode="json"),
            "companion": companion_payload,
        },
    )

    assert extract_error_classifications(error) == ()


def test_wrapping_preserves_existing_classification() -> None:
    original_classification = _user_classification()
    fallback = _platform_classification()
    original = _capture_application_error(
        original_classification,
        {"legacy": "payload"},
    )

    wrapped = _capture_wrapped_application_error(
        original,
        fallback_classification=fallback,
    )

    assert wrapped.type == original_classification.kind.value
    assert wrapped.details == original.details
    assert extract_error_classification(wrapped) == original_classification
    assert extract_error_classification(wrapped) != fallback


@pytest.mark.anyio
async def test_wrapping_drops_outer_details_for_cause_classification() -> None:
    original_classification = _user_classification()
    fallback = _platform_classification()
    classified = _capture_application_error(original_classification)
    try:
        raise ApplicationError(
            "Outer platform failure",
            {"diagnostic": "postgresql://user:secret@example.invalid/database"},
        ) from classified
    except ApplicationError as outer:
        wrapped = _capture_wrapped_application_error(
            outer,
            fallback_classification=fallback,
        )
    failure = Failure()

    await DataConverter.default.encode_failure(wrapped, failure)

    assert len(wrapped.details) == 1
    assert wrapped.details[0]["schema"] == "tracecat.temporal_error.v1"
    assert wrapped.type == original_classification.kind.value
    assert extract_error_classification(wrapped) == original_classification
    assert "secret" not in str(failure)
    assert "example.invalid" not in str(failure)


def test_wrapping_unclassified_application_error_drops_original_details() -> None:
    fallback = _platform_classification()
    original = ApplicationError(
        "Raw platform failure",
        {"diagnostic": "postgresql://user:secret@example.invalid/database"},
    )

    wrapped = _capture_wrapped_application_error(
        original,
        fallback_classification=fallback,
    )

    assert len(wrapped.details) == 1
    assert wrapped.details[0]["schema"] == "tracecat.temporal_error.v1"
    assert "secret" not in str(wrapped.details)
    assert "example.invalid" not in str(wrapped.details)
    assert extract_error_classification(wrapped) == fallback


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
    fallback = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the workflow",
        retry_disposition=retry_disposition,
    )
    original = ApplicationError(
        "Unclassified platform failure",
        next_retry_delay=original_delay,
    )

    wrapped = _capture_wrapped_application_error(
        original,
        fallback_classification=fallback,
    )

    assert wrapped.next_retry_delay == expected_delay
    assert wrapped.non_retryable is (
        retry_disposition is RetryDisposition.NON_RETRYABLE
    )
    assert extract_error_classification(wrapped) == fallback


def test_wrapping_clears_retry_delay_for_non_retryable_cause() -> None:
    classification = _user_classification(RetryDisposition.NON_RETRYABLE)
    classified = _capture_application_error(classification)
    try:
        raise ApplicationError(
            "Outer wrapper",
            next_retry_delay=timedelta(seconds=5),
        ) from classified
    except ApplicationError as outer:
        wrapped = _capture_wrapped_application_error(
            outer,
            fallback_classification=_platform_classification(),
        )

    assert wrapped.next_retry_delay is None
    assert wrapped.non_retryable is True
    assert extract_error_classification(wrapped) == classification


def test_ambiguous_nested_classification_does_not_override_fallback() -> None:
    nested_classification = _user_classification()
    fallback = _platform_classification()
    detail = {
        "classification": nested_classification.model_dump(mode="json"),
        "arbitrary": "outer field",
    }

    error = _capture_application_error(fallback, detail)

    assert error.message == fallback.message
    assert error.non_retryable is False
    assert error.type == fallback.kind.value
    assert error.details[0] == detail
    assert error.details[1]["schema"] == "tracecat.temporal_error.v1"
    assert extract_error_classification(error) == fallback


def test_exception_chain_preserves_runtime_classification() -> None:
    classification = _user_classification()
    classified = TracecatRuntimeError(classification)

    try:
        raise RuntimeError("Outer wrapper") from classified
    except RuntimeError as error:
        assert extract_error_classification(error) == classification


def test_error_owners_remain_attribution_only() -> None:
    assert set(RuntimeErrorOwner) == {
        RuntimeErrorOwner.USER,
        RuntimeErrorOwner.PLATFORM,
    }
