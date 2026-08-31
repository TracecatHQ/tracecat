"""Pure error classification policy for executor activities."""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

from tracecat.exceptions import (
    EntitlementRequired,
    ExecutionError,
    LoopExecutionError,
    ScopeDeniedError,
)
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLeaseContentionError,
    RegistryArtifactExtractionError,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    select_error_classification,
)
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxWorkloadError,
)
from tracecat.sandbox.types import SandboxErrorCode
from tracecat.storage.utils import is_retryable_storage_transport_error
from tracecat.temporal.errors import (
    extract_error_classification,
    iter_error_chain,
)
from tracecat.temporal.exceptions import UserError


def _chained_error_classification(
    error: BaseException,
) -> RuntimeErrorClassification | None:
    """Classify the first known executor or sandbox failure in the chain.

    ``RegistryArtifactCacheLeaseContentionError`` subclasses
    ``RegistryArtifactCacheCapacityError``, so it must be matched first.
    """
    for cause in iter_error_chain(error):
        if isinstance(cause, EntitlementRequired):
            return RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
                message=str(cause),
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=cause,
            )
        if isinstance(cause, RegistryArtifactCacheLeaseContentionError):
            return RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.EXECUTOR_REGISTRY_LEASE_CONTENTION,
                message="Tracecat executor capacity is temporarily unavailable",
                retry_disposition=RetryDisposition.RETRYABLE,
                cause=cause,
            )
        if isinstance(cause, RegistryArtifactCacheCapacityError):
            return RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.EXECUTOR_REGISTRY_CAPACITY_EXHAUSTED,
                message="Tracecat executor artifact capacity is exhausted",
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=cause,
            )
        if isinstance(cause, RegistryArtifactExtractionError):
            return RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.EXECUTOR_REGISTRY_EXTRACTION_FAILED,
                message="Tracecat could not load the action runtime",
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=cause,
            )
        if isinstance(cause, SandboxInfrastructureError):
            return RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.EXECUTOR_SANDBOX_INFRASTRUCTURE_FAILED,
                message="Tracecat could not run the action sandbox",
                retry_disposition=RetryDisposition.RETRYABLE,
                cause=cause,
            )
        if isinstance(cause, SandboxWorkloadError):
            return RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                message="The action sandbox workload stopped before producing a result",
                retry_disposition=(
                    RetryDisposition.RETRYABLE
                    if cause.error_code is SandboxErrorCode.TIMEOUT
                    else RetryDisposition.NON_RETRYABLE
                ),
                cause=cause,
            )
    return None


def _execution_error_classification(
    error: ExecutionError,
) -> RuntimeErrorClassification:
    """Classify one executor invocation failure."""
    return _chained_error_classification(error) or RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message=str(error),
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


def _loop_error_classification(
    error: LoopExecutionError,
) -> RuntimeErrorClassification:
    """Select ownership separately from aggregate loop retryability.

    A retry re-executes the entire for-each batch, so every failed iteration must
    be retryable before the aggregate may be retried. Ownership remains selected
    independently, with platform failures taking precedence for attribution.
    """
    candidates = tuple(
        _execution_error_classification(loop_error) for loop_error in error.loop_errors
    )
    if not candidates:
        return RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
            message=str(error),
            retry_disposition=RetryDisposition.RETRYABLE,
            cause=error,
        )

    selected = select_error_classification(candidates)
    aggregate_retry = (
        RetryDisposition.RETRYABLE
        if all(
            candidate.retry_disposition is RetryDisposition.RETRYABLE
            for candidate in candidates
        )
        else RetryDisposition.NON_RETRYABLE
    )
    return selected.model_copy(update={"retry_disposition": aggregate_retry})


def _application_error_classification(
    error: ApplicationError,
) -> RuntimeErrorClassification:
    """Preserve explicit Temporal classification or adapt its stable flags."""
    if classification := extract_error_classification(error):
        return classification

    retry_disposition = (
        RetryDisposition.NON_RETRYABLE
        if error.non_retryable
        else RetryDisposition.RETRYABLE
    )
    if UserError.matches(error):
        return RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
            message=error.message or "The action failed",
            retry_disposition=retry_disposition,
            cause=error,
        )
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=retry_disposition,
        cause=error,
    )


def classify_execute_action_error(
    error: Exception,
    *,
    action_name: str,
) -> RuntimeErrorClassification:
    """Classify an action error without logging or other side effects."""
    if isinstance(error, ScopeDeniedError):
        return RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
            message=(
                f"Permission denied: missing scope(s) {error.missing_scopes} "
                f"to execute action '{action_name}'"
            ),
            retry_disposition=RetryDisposition.NON_RETRYABLE,
            cause=error,
        )
    if isinstance(error, EntitlementRequired):
        return RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
            message=str(error),
            retry_disposition=RetryDisposition.NON_RETRYABLE,
            cause=error,
        )
    if isinstance(error, ExecutionError):
        return _execution_error_classification(error)
    if isinstance(error, LoopExecutionError):
        return _loop_error_classification(error)
    if isinstance(error, ApplicationError):
        return _application_error_classification(error)
    return _chained_error_classification(error) or RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def executor_backend_initialization_error_classification(
    error: Exception,
) -> RuntimeErrorClassification:
    """Classify an executor backend initialization failure."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.EXECUTOR_BACKEND_INITIALIZATION_FAILED,
        message="Tracecat could not initialize the action executor",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def result_persistence_error_classification(
    error: Exception,
) -> RuntimeErrorClassification:
    """Classify a failure while persisting an action result."""
    transport_failure = is_retryable_storage_transport_error(error)
    return RuntimeErrorClassification.platform(
        kind=(
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE
            if transport_failure
            else RuntimeErrorKind.RUNTIME_UNCLASSIFIED
        ),
        message="Tracecat could not persist the action result",
        # Preserve today's effective fail-fast behavior. Retry policy changes
        # are intentionally handled separately from attribution.
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )
