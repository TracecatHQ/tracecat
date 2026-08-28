"""Temporal activities for the ExecutorWorker.

These activities run on the 'shared-action-queue' and handle action execution
dispatched from DSL workflows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Never

import loguru
from temporalio import activity
from temporalio.exceptions import ApplicationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import backfill_legacy_role_scopes
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.action import materialize_context
from tracecat.dsl.schemas import RunActionInput, StreamID
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import (
    EntitlementRequired,
    ExecutionError,
    LoopExecutionError,
    RateLimitExceeded,
    ScopeDeniedError,
)
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLeaseContentionError,
    RegistryArtifactExtractionError,
)
from tracecat.executor.service import dispatch_action
from tracecat.logger import logger
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxWorkloadError,
)
from tracecat.sandbox.types import SandboxErrorCode
from tracecat.storage.object import StoredObject, action_key, get_object_storage
from tracecat.storage.utils import is_retryable_storage_transport_error
from tracecat.temporal.errors import (
    activity_error_boundary,
    build_error_transport_detail,
    extract_error_classification,
    raise_application_error_from_classification,
)
from tracecat.temporal.exceptions import UserError


def _exception_chain(error: BaseException) -> list[BaseException]:
    """Return an explicit exception chain without following cycles."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _platform_executor_error_classification(
    error: BaseException,
) -> RuntimeErrorClassification | None:
    """Classify known executor-owned failures from their concrete types."""
    for cause in _exception_chain(error):
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
    return None


def _sandbox_workload_error_classification(
    error: BaseException,
) -> RuntimeErrorClassification | None:
    """Classify deterministic sandbox workload exits from their typed code."""
    for cause in _exception_chain(error):
        if not isinstance(cause, SandboxWorkloadError):
            continue
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


def _executor_backend_initialization_error_classification(
    error: Exception,
) -> RuntimeErrorClassification:
    """Classify an executor backend initialization failure."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.EXECUTOR_BACKEND_INITIALIZATION_FAILED,
        message="Tracecat could not initialize the action executor",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def _result_persistence_error_classification(
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


def _raise_classified_executor_application_error(
    *,
    classification: RuntimeErrorClassification,
    detail_type: str,
    ref: str,
    attempt: int,
    stream_id: StreamID,
    details: tuple[Any, ...] = (),
    next_retry_delay: timedelta | None = None,
) -> Never:
    """Raise the scheduler-compatible classified activity failure."""
    err_info = ActionErrorInfo(
        ref=ref,
        message=classification.message,
        type=detail_type,
        attempt=attempt,
        stream_id=stream_id,
    )
    raise_application_error_from_classification(
        classification,
        build_error_transport_detail(classification, err_info),
        *details,
        next_retry_delay=(
            next_retry_delay
            if classification.retry_disposition is RetryDisposition.RETRYABLE
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class _ExecuteActionErrorClassification:
    classification: RuntimeErrorClassification
    detail_type: str
    details: tuple[Any, ...] = ()
    next_retry_delay: timedelta | None = None


def _classify_execute_action_error(
    error: Exception,
    *,
    action_name: str,
    log: loguru.Logger,
) -> _ExecuteActionErrorClassification:
    if isinstance(error, ScopeDeniedError):
        message = f"Permission denied: missing scope(s) {error.missing_scopes} to execute action '{action_name}'"
        log.warning(
            "Action scope denied",
            action=action_name,
            required_scopes=error.required_scopes,
            missing_scopes=error.missing_scopes,
        )
        return _ExecuteActionErrorClassification(
            classification=RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                message=message,
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=error,
            ),
            detail_type=error.__class__.__name__,
        )

    if isinstance(error, EntitlementRequired):
        message = str(error)
        log.warning("Action entitlement denied", action=action_name, error=message)
        return _ExecuteActionErrorClassification(
            classification=RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
                message=message,
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=error,
            ),
            detail_type=error.__class__.__name__,
        )

    if isinstance(error, ExecutionError):
        if classification := _platform_executor_error_classification(error):
            log.error(
                "Platform error executing action",
                error=error,
                cause_type=classification.cause_type,
            )
            return _ExecuteActionErrorClassification(
                classification=classification,
                detail_type=error.__class__.__name__,
            )
        if classification := _sandbox_workload_error_classification(error):
            log.info(
                "Sandbox workload failed",
                error=error,
                cause_type=classification.cause_type,
            )
            return _ExecuteActionErrorClassification(
                classification=classification,
                detail_type=error.__class__.__name__,
            )
        message = str(error)
        log.info("Execution error", error=message, info=error.info)
        return _ExecuteActionErrorClassification(
            classification=RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                message=message,
                retry_disposition=RetryDisposition.RETRYABLE,
                cause=error,
            ),
            detail_type=error.__class__.__name__,
        )

    if isinstance(error, LoopExecutionError):
        platform_classification = next(
            (
                classification
                for loop_error in error.loop_errors
                if (
                    classification := _platform_executor_error_classification(
                        loop_error
                    )
                )
                is not None
            ),
            None,
        )
        if platform_classification is not None:
            return _ExecuteActionErrorClassification(
                classification=platform_classification,
                detail_type=error.__class__.__name__,
            )
        workload_classification = next(
            (
                classification
                for loop_error in error.loop_errors
                if (
                    classification := _sandbox_workload_error_classification(loop_error)
                )
                is not None
            ),
            None,
        )
        if workload_classification is not None:
            return _ExecuteActionErrorClassification(
                classification=workload_classification,
                detail_type=error.__class__.__name__,
            )
        message = str(error)
        log.info("Loop execution error", error=message, loop_errors=error.loop_errors)
        return _ExecuteActionErrorClassification(
            classification=RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                message=message,
                retry_disposition=RetryDisposition.RETRYABLE,
                cause=error,
            ),
            detail_type=error.__class__.__name__,
        )

    if isinstance(error, ApplicationError):
        log.error("ApplicationError occurred", error=error)
        if classification := extract_error_classification(error):
            return _ExecuteActionErrorClassification(
                classification=classification,
                detail_type=error.type or error.__class__.__name__,
                details=tuple(error.details),
                next_retry_delay=error.next_retry_delay,
            )
        retry_disposition = (
            RetryDisposition.NON_RETRYABLE
            if error.non_retryable
            else RetryDisposition.RETRYABLE
        )
        if UserError.matches(error):
            classification = RuntimeErrorClassification.user(
                kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                message=error.message or "The action failed",
                retry_disposition=retry_disposition,
                cause=error,
            )
        else:
            classification = RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
                message="Tracecat could not execute the action",
                retry_disposition=retry_disposition,
                cause=error,
            )
        return _ExecuteActionErrorClassification(
            classification=classification,
            detail_type=error.type or error.__class__.__name__,
            next_retry_delay=error.next_retry_delay,
        )

    if classification := _platform_executor_error_classification(error):
        log.error(
            "Platform error executing action",
            error=error,
            cause_type=classification.cause_type,
        )
        return _ExecuteActionErrorClassification(
            classification=classification,
            detail_type=error.__class__.__name__,
        )
    if classification := _sandbox_workload_error_classification(error):
        log.info(
            "Sandbox workload failed",
            error=error,
            cause_type=classification.cause_type,
        )
        return _ExecuteActionErrorClassification(
            classification=classification,
            detail_type=error.__class__.__name__,
        )

    cause_type = error.__class__.__name__
    log.error(
        "Unexpected error executing action",
        error=error,
        error_type=cause_type,
    )
    return _ExecuteActionErrorClassification(
        classification=RuntimeErrorClassification.platform(
            kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
            message="Tracecat could not execute the action",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
            cause=error,
        ),
        detail_type=cause_type,
    )


async def _heartbeat_loop(interval: int, task_ref: str, action_name: str) -> None:
    """Send periodic heartbeats to Temporal until cancelled.

    Runs as a background asyncio task alongside the long-running
    dispatch_action() call. Cancelled by the caller when dispatch completes.
    """
    elapsed = 0
    try:
        while True:
            await asyncio.sleep(interval)
            elapsed += interval
            activity.heartbeat(f"{action_name} ({task_ref}): {elapsed}s elapsed")
    except asyncio.CancelledError:
        pass


class ExecutorActivities:
    """Container for executor activities."""

    def __new__(cls) -> None:
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        """Load and return all activities in the class."""
        return [
            fn
            for method_name in dir(cls)
            if hasattr(
                fn := getattr(cls, method_name),
                "__temporal_activity_definition",
            )
        ]

    @staticmethod
    @activity.defn
    async def execute_action_activity(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        """Execute an action on the ExecutorWorker.

        This activity runs on 'shared-action-queue' and handles:
        - Rate limit retries (tenacity)
        - for_each loop execution (via dispatch_action)
        - Sandboxed action execution

        This replaces the HTTP-based run_action_activity from dsl/action.py.
        Secrets/variables are still handled inside the sandbox (Phase 2 will move them here).
        """
        ctx_run.set(input.run_context)
        # Backfill scopes for roles serialized before the RBAC migration.
        # Temporal history may contain Role objects with empty/None scopes.
        role = backfill_legacy_role_scopes(role)
        ctx_role.set(role)

        task = input.task
        environment = input.run_context.environment
        action_name = task.action

        log = logger.bind(
            task_ref=task.ref,
            action_name=action_name,
            wf_id=input.run_context.wf_id,
            role=role,
            environment=environment,
        )
        ctx_logger.set(log)

        act_info = activity.info()
        act_attempt = act_info.attempt
        log.debug(
            "Execute action activity details",
            task=task,
            attempt=act_attempt,
            retry_policy=task.retry_policy,
            input=input,
        )
        materialized_input = input.model_copy(
            update={"exec_context": await materialize_context(input.exec_context)}
        )

        heartbeat_interval = config.TRACECAT__ACTIVITY_HEARTBEAT_INTERVAL

        # Run a background heartbeat task for the full activity lifetime
        # (including tenacity backoff sleeps) so Temporal can detect a dead
        # worker without waiting for start_to_close_timeout.
        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval > 0:
            activity.heartbeat(f"{action_name} ({task.ref}) starting")
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(heartbeat_interval, task.ref, action_name)
            )

        try:
            with activity_error_boundary(
                _executor_backend_initialization_error_classification
            ):
                backend = get_executor_backend()

            async for attempt_manager in AsyncRetrying(
                retry=retry_if_exception_type(RateLimitExceeded),
                stop=stop_after_attempt(20),
                wait=wait_exponential(min=4, max=300),
            ):
                with attempt_manager:
                    log.debug(
                        "Begin action attempt",
                        attempt_number=attempt_manager.retry_state.attempt_number,
                    )
                    result = await dispatch_action(
                        backend=backend, input=materialized_input
                    )

                    if heartbeat_interval > 0:
                        activity.heartbeat(
                            f"{action_name} ({task.ref}) completed, storing result"
                        )

                    # Always wrap result in StoredObject envelope
                    # - get_object_storage() returns S3ObjectStorage when externalization is enabled
                    #   (externalizes if above threshold), else InlineObjectStorage (always inline)
                    key = action_key(
                        workspace_id=str(role.workspace_id),
                        wf_exec_id=input.run_context.wf_exec_id,
                        stream_id=input.stream_id,
                        ref=task.ref,
                    )
                    with activity_error_boundary(
                        _result_persistence_error_classification
                    ):
                        stored = await get_object_storage().store(key, result)
                    return stored
        except Exception as e:
            error_classification = _classify_execute_action_error(
                e,
                action_name=action_name,
                log=log,
            )
            _raise_classified_executor_application_error(
                classification=error_classification.classification,
                detail_type=error_classification.detail_type,
                ref=task.ref,
                attempt=act_attempt,
                stream_id=input.stream_id,
                details=error_classification.details,
                next_retry_delay=error_classification.next_retry_delay,
            )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        # Unreachable: AsyncRetrying either returns in the loop or raises RetryError
        # (caught by Exception handler above) when retries are exhausted
        raise AssertionError("Unreachable: AsyncRetrying loop must return or raise")
