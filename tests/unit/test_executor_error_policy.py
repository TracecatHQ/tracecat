"""Focused tests for executor error classification policy."""

from __future__ import annotations

import pytest

from tracecat.exceptions import EntitlementRequired, ExecutionError, LoopExecutionError
from tracecat.executor.error_policy import classify_execute_action_error
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheLeaseContentionError,
)
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.exceptions import (
    SandboxWorkloadError,
    raise_for_sandbox_error_code,
)
from tracecat.sandbox.types import SandboxErrorCode


def _iteration_error(cause: Exception, *, index: int) -> ExecutionError:
    error = ExecutionError(
        info=ExecutorActionErrorInfo(
            type=type(cause).__name__,
            message="masked loop error",
            action_name="test_action",
            filename="<test>",
            function="test_function",
            loop_iteration=index,
        )
    )
    error.__cause__ = cause
    return error


def _lease_contention() -> RegistryArtifactCacheLeaseContentionError:
    return RegistryArtifactCacheLeaseContentionError(
        current_bytes=80,
        additional_bytes=30,
        max_bytes=100,
    )


def test_wrapped_entitlement_failure_is_non_retryable_user_error() -> None:
    cause = EntitlementRequired("synthetic_feature")

    result = classify_execute_action_error(
        _iteration_error(cause, index=0),
        action_name="test_action",
    )

    assert result.owner is RuntimeErrorOwner.USER
    assert result.kind is RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED
    assert result.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert result.cause_type == "EntitlementRequired"


def test_raw_custom_action_exception_is_user_owned_action_failure() -> None:
    """A custom action bug is owned by its author, not platform operations."""
    cause = AttributeError("synthetic custom action failure")
    error = _iteration_error(cause, index=0)

    result = classify_execute_action_error(
        error,
        action_name="custom_actions.synthetic.fetch_data",
    )

    assert result.owner is RuntimeErrorOwner.USER
    assert result.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    assert result.retry_disposition is RetryDisposition.RETRYABLE
    assert result.cause_type == "ExecutionError"


@pytest.mark.parametrize("platform_first", [True, False])
def test_mixed_loop_failure_is_platform_owned_but_non_retryable(
    platform_first: bool,
) -> None:
    """A non-retryable iteration prevents retrying the whole side-effecting batch."""
    platform_error = _iteration_error(_lease_contention(), index=0)
    workload_error = _iteration_error(
        SandboxWorkloadError(
            "synthetic workload diagnostic",
            error_code=SandboxErrorCode.POLICY_VIOLATION,
        ),
        index=1,
    )
    loop_errors = (
        [platform_error, workload_error]
        if platform_first
        else [workload_error, platform_error]
    )

    result = classify_execute_action_error(
        LoopExecutionError(loop_errors),
        action_name="test_action",
    )

    assert result.owner is RuntimeErrorOwner.PLATFORM
    assert result.kind is RuntimeErrorKind.EXECUTOR_REGISTRY_LEASE_CONTENTION
    assert result.retry_disposition is RetryDisposition.NON_RETRYABLE


def test_loop_retry_requires_every_failed_iteration_to_be_retryable() -> None:
    loop_error = LoopExecutionError(
        [
            _iteration_error(_lease_contention(), index=0),
            _iteration_error(
                SandboxWorkloadError(
                    "synthetic timeout diagnostic",
                    error_code=SandboxErrorCode.TIMEOUT,
                ),
                index=1,
            ),
        ]
    )

    result = classify_execute_action_error(
        loop_error,
        action_name="test_action",
    )

    assert result.owner is RuntimeErrorOwner.PLATFORM
    assert result.retry_disposition is RetryDisposition.RETRYABLE


def test_sandbox_resource_limit_gets_dedicated_user_owned_kind() -> None:
    """A workload that exceeded a published sandbox cap is the caller's to fix.

    Invariant: ``SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED`` maps to the
    dedicated ``sandbox.resource_limit_exceeded`` kind, stays user-owned, is
    never retried (the cap is deterministic), and names the memory env var.
    """
    workload_error = SandboxWorkloadError(
        "sandbox-controlled text",
        error_code=SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
    )

    classification = classify_execute_action_error(
        _iteration_error(workload_error, index=0),
        action_name="test_action",
    )

    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.cause_type == "SandboxWorkloadError"
    assert "TRACECAT__SANDBOX_DEFAULT_MEMORY_MB" in classification.message
    assert "sandbox-controlled text" not in classification.message


@pytest.mark.parametrize(
    ("error_code", "retry_disposition"),
    [
        (SandboxErrorCode.WORKLOAD_FAILURE, RetryDisposition.NON_RETRYABLE),
        (SandboxErrorCode.POLICY_VIOLATION, RetryDisposition.NON_RETRYABLE),
        (SandboxErrorCode.TIMEOUT, RetryDisposition.RETRYABLE),
    ],
)
def test_other_sandbox_workload_codes_keep_action_execution_failed(
    error_code: SandboxErrorCode,
    retry_disposition: RetryDisposition,
) -> None:
    """Invariant: only the resource-limit code leaves ``action.execution.failed``."""
    workload_error = SandboxWorkloadError("stopped", error_code=error_code)

    classification = classify_execute_action_error(
        _iteration_error(workload_error, index=0),
        action_name="test_action",
    )

    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    assert classification.retry_disposition is retry_disposition


def test_resource_limit_envelope_code_reaches_classification_without_text() -> None:
    """Invariant: the envelope's ``resource_limit_exceeded`` code alone drives it.

    ``raise_for_sandbox_error_code`` is the only bridge from a decoded sandbox
    result to the executor error chain, so the code it receives from an
    in-jail ``MemoryError`` envelope must survive as a typed exception.
    """
    with pytest.raises(SandboxWorkloadError) as excinfo:
        raise_for_sandbox_error_code(
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "MemoryError: script exceeded the sandbox memory limit",
        )

    assert excinfo.value.error_code is SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED
    classification = classify_execute_action_error(
        _iteration_error(excinfo.value, index=0),
        action_name="core.script.run_python",
    )
    assert classification.kind is RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED
