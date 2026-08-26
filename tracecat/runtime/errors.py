"""Stable runtime error attribution and retry contract.

Runtime errors have independent product dimensions:

* ``owner`` assigns operational ownership for observability and alerting.
* ``kind`` identifies the stable product failure boundary and reason.
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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ERROR_ENVELOPE_SCHEMA = "tracecat.error.v1"


class RuntimeErrorOwner(StrEnum):
    """Operational owner of a runtime failure."""

    USER = "user"
    PLATFORM = "platform"


class RetryDisposition(StrEnum):
    """Whether the failed operation may be attempted again."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class RuntimeErrorKind(StrEnum):
    """Stable machine-readable product failure identities."""

    ACTION_EXECUTION_FAILED = "action.execution.failed"
    TENANT_QUOTA_EXHAUSTED = "tenant.quota.exhausted"
    TENANT_ENTITLEMENT_DENIED = "tenant.entitlement.denied"
    INTEGRATION_RATE_LIMITED = "integration.rate_limited"
    RUNTIME_UNCLASSIFIED = "runtime.unclassified"
    STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE = (
        "storage.materialization.transport_unavailable"
    )
    STORAGE_MATERIALIZATION_INVALID_DATA = "storage.materialization.invalid_data"
    STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE = (
        "storage.persistence.transport_unavailable"
    )
    EXECUTOR_BACKEND_INITIALIZATION_FAILED = "executor.backend.initialization_failed"
    EXECUTOR_REGISTRY_LEASE_CONTENTION = "executor.registry.lease_contention"
    EXECUTOR_REGISTRY_CAPACITY_EXHAUSTED = "executor.registry.capacity_exhausted"
    EXECUTOR_REGISTRY_EXTRACTION_FAILED = "executor.registry.extraction_failed"
    EXECUTOR_SANDBOX_INFRASTRUCTURE_FAILED = "executor.sandbox.infrastructure_failed"
    WORKFLOW_DEFINITION_NOT_FOUND = "workflow.definition.not_found"
    WORKFLOW_DEFINITION_LOOKUP_UNAVAILABLE = "workflow.definition.lookup_unavailable"
    WORKFLOW_SUBFLOW_INPUT_INVALID = "workflow.subflow.input_invalid"
    WORKFLOW_SUBFLOW_PREPARATION_FAILED = "workflow.subflow.preparation_failed"


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
    kind: RuntimeErrorKind
    message: str
    retry_disposition: RetryDisposition
    cause_type: str | None = None

    @classmethod
    def user(
        cls,
        *,
        kind: RuntimeErrorKind,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None = None,
    ) -> ErrorEnvelope:
        """Construct a user-attributed error from an explicit enum kind."""
        cls._require_kind(kind)
        cls._require_retry_disposition(retry_disposition)
        return cls.model_validate(
            {
                "schema": ERROR_ENVELOPE_SCHEMA,
                "owner": RuntimeErrorOwner.USER,
                "kind": kind,
                "message": message,
                "retry_disposition": retry_disposition,
                "cause_type": type(cause).__name__ if cause is not None else None,
            }
        )

    @classmethod
    def platform(
        cls,
        *,
        kind: RuntimeErrorKind,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None = None,
    ) -> ErrorEnvelope:
        """Construct a platform-attributed error from an explicit enum kind."""
        cls._require_kind(kind)
        cls._require_retry_disposition(retry_disposition)
        return cls.model_validate(
            {
                "schema": ERROR_ENVELOPE_SCHEMA,
                "owner": RuntimeErrorOwner.PLATFORM,
                "kind": kind,
                "message": message,
                "retry_disposition": retry_disposition,
                "cause_type": type(cause).__name__ if cause is not None else None,
            }
        )

    @staticmethod
    def _require_kind(kind: RuntimeErrorKind) -> None:
        if not isinstance(kind, RuntimeErrorKind):
            raise TypeError("kind must be a RuntimeErrorKind enum member")

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
