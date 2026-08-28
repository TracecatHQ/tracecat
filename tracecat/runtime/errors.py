"""Stable runtime error attribution and retry contract.

Runtime errors have independent product dimensions:

* ``owner`` assigns operational ownership for observability and alerting.
* ``kind`` identifies the stable product failure boundary and reason.
* ``retry_disposition`` describes whether another attempt is allowed.
* Workflow control flow remains owned by the workflow definition and engine.

The classification intentionally excludes transport details, execution
identifiers, raw platform diagnostics, and routing instructions. Future
classification versions must use a new ``schema`` literal and a discriminated
union rather than adding a second version field.

Attribution is scoped to each Temporal Workflow Execution. A terminal
platform-attributed child failure remains independently attributable and
alertable even when its parent handles that failure and completes. Diagnostic
``cause_type`` values must never select retry, routing, or alert policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RUNTIME_ERROR_CLASSIFICATION_SCHEMA = "tracecat.error.v1"


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
    WORKFLOW_DEFINITION_INVALID_DATA = "workflow.definition.invalid_data"
    WORKFLOW_SUBFLOW_INPUT_INVALID = "workflow.subflow.input_invalid"
    WORKFLOW_SUBFLOW_PREPARATION_FAILED = "workflow.subflow.preparation_failed"


class RuntimeErrorClassification(BaseModel):
    """Versioned error attribution and retry metadata.

    User messages must be masked before construction. Platform messages must be
    neutral and safe for durable history; pass the original exception as
    ``cause`` to a named constructor to retain only its type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    # The alias is the public contract. The suffix avoids overriding Pydantic's
    # deprecated ``BaseModel.schema()`` method while keeping the wire key exact.
    # The field is deliberately required: validation itself then rejects
    # undiscriminated payloads, and a required field always survives
    # ``exclude_unset`` serialization onto the Temporal wire.
    schema_: Literal["tracecat.error.v1"] = Field(alias="schema")
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
    ) -> RuntimeErrorClassification:
        """Construct a user-attributed error from an explicit enum kind."""
        return cls._build(
            RuntimeErrorOwner.USER,
            kind=kind,
            message=message,
            retry_disposition=retry_disposition,
            cause=cause,
        )

    @classmethod
    def platform(
        cls,
        *,
        kind: RuntimeErrorKind,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None = None,
    ) -> RuntimeErrorClassification:
        """Construct a platform-attributed error from an explicit enum kind."""
        return cls._build(
            RuntimeErrorOwner.PLATFORM,
            kind=kind,
            message=message,
            retry_disposition=retry_disposition,
            cause=cause,
        )

    @classmethod
    def _build(
        cls,
        owner: RuntimeErrorOwner,
        *,
        kind: RuntimeErrorKind,
        message: str,
        retry_disposition: RetryDisposition,
        cause: BaseException | None,
    ) -> RuntimeErrorClassification:
        return cls.model_validate(
            {
                "schema": RUNTIME_ERROR_CLASSIFICATION_SCHEMA,
                "owner": owner,
                "kind": kind,
                "message": message,
                "retry_disposition": retry_disposition,
                "cause_type": type(cause).__name__ if cause is not None else None,
            }
        )


class TracecatRuntimeError(Exception):
    """Exception carrying an already-classified runtime error."""

    def __init__(self, classification: RuntimeErrorClassification) -> None:
        super().__init__(classification.message)
        self.classification = classification


def select_error_classification(
    classifications: Iterable[RuntimeErrorClassification],
) -> RuntimeErrorClassification:
    """Select the first platform-owned classification, otherwise the first."""
    candidates = tuple(classifications)
    if not candidates:
        raise ValueError("Cannot select from an empty error classification collection")
    return next(
        (
            classification
            for classification in candidates
            if classification.owner is RuntimeErrorOwner.PLATFORM
        ),
        candidates[0],
    )


def parse_error_classification(value: object) -> RuntimeErrorClassification | None:
    """Parse only payloads carrying the explicit classification discriminator.

    The required ``schema`` field makes validation the discriminator check:
    payloads without it fail to parse.
    """
    if isinstance(value, RuntimeErrorClassification):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return RuntimeErrorClassification.model_validate(value)
    except ValidationError:
        return None
