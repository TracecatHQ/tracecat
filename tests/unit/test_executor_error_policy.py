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
from tracecat.sandbox.exceptions import SandboxWorkloadError
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
