from __future__ import annotations

import pytest
from pydantic import ValidationError

from tracecat.runtime.errors import (
    RUNTIME_ERROR_CLASSIFICATION_SCHEMA,
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
    parse_error_classification,
    select_error_classification,
)


@pytest.mark.parametrize("kind", list(RuntimeErrorKind))
@pytest.mark.parametrize("owner", list(RuntimeErrorOwner))
@pytest.mark.parametrize("retry_disposition", list(RetryDisposition))
def test_error_classification_supports_every_kind_owner_and_retry_disposition(
    kind: RuntimeErrorKind,
    owner: RuntimeErrorOwner,
    retry_disposition: RetryDisposition,
) -> None:
    factory = (
        RuntimeErrorClassification.user
        if owner is RuntimeErrorOwner.USER
        else RuntimeErrorClassification.platform
    )

    classification = factory(
        kind=kind,
        message="Safe runtime error",
        retry_disposition=retry_disposition,
    )

    assert classification.schema_ == RUNTIME_ERROR_CLASSIFICATION_SCHEMA
    assert classification.owner is owner
    assert classification.kind is kind
    assert classification.retry_disposition is retry_disposition


def test_error_classification_serializes_exact_contract() -> None:
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    assert classification.model_dump(mode="json") == {
        "schema": "tracecat.error.v1",
        "owner": "user",
        "kind": "action.execution.failed",
        "message": "The action failed",
        "retry_disposition": "non_retryable",
        "cause_type": None,
    }


def test_named_constructor_keeps_schema_when_excluding_unset_fields() -> None:
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    assert classification.model_dump(mode="json", exclude_unset=True)["schema"] == (
        RUNTIME_ERROR_CLASSIFICATION_SCHEMA
    )


def test_error_classification_rejects_extra_version_field() -> None:
    with pytest.raises(ValidationError):
        RuntimeErrorClassification.model_validate(
            {
                "schema": "tracecat.error.v1",
                "version": 1,
                "owner": "user",
                "kind": "action.execution.failed",
                "message": "The action failed",
                "retry_disposition": "non_retryable",
            }
        )


def test_parser_requires_explicit_schema_discriminator() -> None:
    serialized = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    ).model_dump(mode="json")
    missing_schema = {
        key: value for key, value in serialized.items() if key != "schema"
    }

    assert parse_error_classification(serialized) is not None
    assert parse_error_classification(missing_schema) is None
    assert (
        parse_error_classification({**serialized, "schema": "tracecat.error.v2"})
        is None
    )


def test_select_error_classification_prefers_first_platform_classification() -> None:
    first_user = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="First user error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    first_platform = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="First platform error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    second_platform = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.WORKFLOW_SUBFLOW_PREPARATION_FAILED,
        message="Second platform error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    assert (
        select_error_classification((first_user, first_platform, second_platform))
        is first_platform
    )


def test_select_error_classification_keeps_first_user_without_platform() -> None:
    first_user = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="First user error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    second_user = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.WORKFLOW_SUBFLOW_INPUT_INVALID,
        message="Second user error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    assert select_error_classification((first_user, second_user)) is first_user


def test_select_error_classification_rejects_empty_collection() -> None:
    with pytest.raises(ValueError, match="empty error classification collection"):
        select_error_classification(())


def test_platform_classification_excludes_sensitive_cause_message() -> None:
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")

    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        message="A platform dependency is unavailable",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=sensitive,
    )

    serialized = classification.model_dump_json()
    assert classification.cause_type == "RuntimeError"
    assert "secret" not in serialized
    assert "example.invalid" not in serialized
