from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from tracecat.runtime.errors import (
    ERROR_ENVELOPE_SCHEMA,
    ErrorEnvelope,
    RetryDisposition,
    RuntimeErrorCode,
    RuntimeErrorOwner,
    parse_error_envelope,
)


@pytest.mark.parametrize(
    ("code", "owner"),
    [
        (RuntimeErrorCode.USER_ACTION_FAILED, RuntimeErrorOwner.USER),
        (RuntimeErrorCode.TENANT_QUOTA_EXHAUSTED, RuntimeErrorOwner.USER),
        (RuntimeErrorCode.TENANT_ENTITLEMENT_DENIED, RuntimeErrorOwner.USER),
        (RuntimeErrorCode.INTEGRATION_RATE_LIMITED, RuntimeErrorOwner.USER),
        (RuntimeErrorCode.PLATFORM_UNCLASSIFIED, RuntimeErrorOwner.PLATFORM),
        (
            RuntimeErrorCode.PLATFORM_DEPENDENCY_UNAVAILABLE,
            RuntimeErrorOwner.PLATFORM,
        ),
        (RuntimeErrorCode.PLATFORM_CAPACITY_EXHAUSTED, RuntimeErrorOwner.PLATFORM),
    ],
)
@pytest.mark.parametrize("retry_disposition", list(RetryDisposition))
def test_error_envelope_supports_every_code_and_retry_disposition(
    code: RuntimeErrorCode,
    owner: RuntimeErrorOwner,
    retry_disposition: RetryDisposition,
) -> None:
    factory = (
        ErrorEnvelope.user
        if owner is RuntimeErrorOwner.USER
        else ErrorEnvelope.platform
    )

    envelope = factory(
        code=code,
        message="Safe runtime error",
        retry_disposition=retry_disposition,
    )

    assert envelope.schema_ == ERROR_ENVELOPE_SCHEMA
    assert envelope.owner is owner
    assert envelope.code is code
    assert envelope.retry_disposition is retry_disposition


def test_error_envelope_serializes_exact_contract() -> None:
    envelope = ErrorEnvelope.user(
        code=RuntimeErrorCode.USER_ACTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    assert envelope.model_dump(mode="json") == {
        "schema": "tracecat.error.v1",
        "owner": "user",
        "code": "user.action.failed",
        "message": "The action failed",
        "retry_disposition": "non_retryable",
        "cause_type": None,
    }


def test_error_envelope_rejects_extra_version_field() -> None:
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate(
            {
                "schema": "tracecat.error.v1",
                "version": 1,
                "owner": "user",
                "code": "user.action.failed",
                "message": "The action failed",
                "retry_disposition": "non_retryable",
            }
        )


def test_parser_requires_explicit_schema_discriminator() -> None:
    serialized = ErrorEnvelope.user(
        code=RuntimeErrorCode.USER_ACTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    ).model_dump(mode="json")
    missing_schema = {
        key: value for key, value in serialized.items() if key != "schema"
    }

    assert parse_error_envelope(serialized) is not None
    assert parse_error_envelope(missing_schema) is None
    assert parse_error_envelope({**serialized, "schema": "tracecat.error.v2"}) is None


def test_named_constructors_require_enum_members() -> None:
    with pytest.raises(TypeError, match="RuntimeErrorCode enum member"):
        ErrorEnvelope.user(
            code=cast(RuntimeErrorCode, "user.action.failed"),
            message="The action failed",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        )

    with pytest.raises(TypeError, match="RetryDisposition enum member"):
        ErrorEnvelope.user(
            code=RuntimeErrorCode.USER_ACTION_FAILED,
            message="The action failed",
            retry_disposition=cast(RetryDisposition, "non_retryable"),
        )


@pytest.mark.parametrize(
    ("owner", "code"),
    [
        (RuntimeErrorOwner.USER, RuntimeErrorCode.PLATFORM_UNCLASSIFIED),
        (RuntimeErrorOwner.PLATFORM, RuntimeErrorCode.USER_ACTION_FAILED),
    ],
)
def test_error_envelope_rejects_mismatched_owner_and_code(
    owner: RuntimeErrorOwner, code: RuntimeErrorCode
) -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        ErrorEnvelope(
            owner=owner,
            code=code,
            message="Safe runtime error",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        )


def test_platform_envelope_excludes_sensitive_cause_message() -> None:
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")

    envelope = ErrorEnvelope.platform(
        code=RuntimeErrorCode.PLATFORM_DEPENDENCY_UNAVAILABLE,
        message="A platform dependency is unavailable",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=sensitive,
    )

    serialized = envelope.model_dump_json()
    assert envelope.cause_type == "RuntimeError"
    assert "secret" not in serialized
    assert "example.invalid" not in serialized
