"""Stable runtime error attribution and retry contract.

Runtime errors have three independent axes:

* ``owner`` assigns operational ownership for observability and alerting.
* ``retry_disposition`` describes whether another attempt is allowed.
* Workflow control flow remains owned by the workflow definition and engine.

The contract intentionally excludes transport details, execution identifiers,
raw platform diagnostics, and routing instructions. Future envelope versions
must use a new ``schema`` literal and a discriminated union rather than adding a
second version field.

Attribution is scoped to each Temporal Workflow Execution. A terminal
platform-attributed child failure remains independently attributable and
alertable even when its parent handles that failure and completes. Diagnostic
``cause_type`` values must never select retry, routing, or alert policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ERROR_ENVELOPE_SCHEMA = "tracecat.error.v1"


class RuntimeErrorOwner(StrEnum):
    """Operational owner of a runtime failure."""

    USER = "user"
    PLATFORM = "platform"


class RetryDisposition(StrEnum):
    """Whether the failed operation may be attempted again."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class RuntimeErrorCode(StrEnum):
    """Stable machine-readable runtime failure codes."""

    USER_ACTION_FAILED = "user.action.failed"
    TENANT_QUOTA_EXHAUSTED = "tenant.quota.exhausted"
    TENANT_ENTITLEMENT_DENIED = "tenant.entitlement.denied"
    INTEGRATION_RATE_LIMITED = "integration.rate_limited"
    PLATFORM_UNCLASSIFIED = "platform.unclassified"
    PLATFORM_DEPENDENCY_UNAVAILABLE = "platform.dependency.unavailable"
    PLATFORM_CAPACITY_EXHAUSTED = "platform.capacity.exhausted"

    @property
    def owner(self) -> RuntimeErrorOwner:
        """Return the owner implied by this code's namespace."""
        if self.value.startswith("platform."):
            return RuntimeErrorOwner.PLATFORM
        return RuntimeErrorOwner.USER


class ErrorEnvelope(BaseModel):
    """Versioned error attribution and retry metadata.

    User messages must be masked before construction. Platform messages must be
    neutral and safe for durable history; pass the original exception as
    ``cause`` to a named constructor to retain only its type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    # The alias is the public contract. The suffix avoids overriding Pydantic's
    # deprecated ``BaseModel.schema()`` method while keeping the wire key exact.
    schema_: Literal["tracecat.error.v1"] = Field(
        default=ERROR_ENVELOPE_SCHEMA,
        alias="schema",
    )
    owner: RuntimeErrorOwner
    code: RuntimeErrorCode
    message: str
    retry_disposition: RetryDisposition
    cause_type: str | None = None

    @model_validator(mode="after")
    def validate_code_owner(self) -> Self:
        """Reject contradictory ownership and code namespaces."""
        if self.code.owner is not self.owner:
            raise ValueError(
                f"Error code {self.code.value!r} does not belong to "
                f"owner {self.owner.value!r}"
            )
        return self

    @classmethod
    def user(
        cls,
        *,
        code: RuntimeErrorCode,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None = None,
    ) -> ErrorEnvelope:
        """Construct a user-attributed error from an explicit enum code."""
        cls._require_code_owner(code, RuntimeErrorOwner.USER)
        cls._require_retry_disposition(retry_disposition)
        return cls(
            owner=RuntimeErrorOwner.USER,
            code=code,
            message=message,
            retry_disposition=retry_disposition,
            cause_type=type(cause).__name__ if cause is not None else None,
        )

    @classmethod
    def platform(
        cls,
        *,
        code: RuntimeErrorCode,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None = None,
    ) -> ErrorEnvelope:
        """Construct a platform-attributed error from an explicit enum code."""
        cls._require_code_owner(code, RuntimeErrorOwner.PLATFORM)
        cls._require_retry_disposition(retry_disposition)
        return cls(
            owner=RuntimeErrorOwner.PLATFORM,
            code=code,
            message=message,
            retry_disposition=retry_disposition,
            cause_type=type(cause).__name__ if cause is not None else None,
        )

    @staticmethod
    def _require_code_owner(
        code: RuntimeErrorCode, expected_owner: RuntimeErrorOwner
    ) -> None:
        if not isinstance(code, RuntimeErrorCode):
            raise TypeError("code must be a RuntimeErrorCode enum member")
        if code.owner is not expected_owner:
            raise ValueError(
                f"Error code {code.value!r} does not belong to "
                f"owner {expected_owner.value!r}"
            )

    @staticmethod
    def _require_retry_disposition(
        retry_disposition: RetryDisposition,
    ) -> None:
        if not isinstance(retry_disposition, RetryDisposition):
            raise TypeError("retry_disposition must be a RetryDisposition enum member")


class TracecatRuntimeError(Exception):
    """Exception carrying an already-classified runtime error envelope."""

    def __init__(self, envelope: ErrorEnvelope) -> None:
        super().__init__(envelope.message)
        self.envelope = envelope


def parse_error_envelope(value: object) -> ErrorEnvelope | None:
    """Parse only payloads carrying the explicit envelope discriminator."""
    if isinstance(value, ErrorEnvelope):
        return value
    if not isinstance(value, Mapping):
        return None
    if value.get("schema") != ERROR_ENVELOPE_SCHEMA:
        return None
    try:
        return ErrorEnvelope.model_validate(value)
    except ValidationError:
        return None
