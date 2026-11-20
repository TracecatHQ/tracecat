"""Temporal activities for the ExecutorWorker.

These activities run on the 'shared-action-queue' and handle action execution
dispatched from DSL workflows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from temporalio import activity
from temporalio.exceptions import ApplicationError, is_cancelled_exception
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat import config
from tracecat.admission import gate
from tracecat.admission.gate import ConcurrencyBusyError, Lease
from tracecat.admission.types import (
    ADMISSION_ACK_SIGNAL,
    ConcurrencyBusyResult,
    ConcurrencyResource,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import backfill_legacy_role_scopes
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.action import materialize_context
from tracecat.dsl.schemas import RunActionInput
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import ExecutionError, LoopExecutionError, RateLimitExceeded
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.error_policy import (
    classify_execute_action_error,
    executor_backend_initialization_error_classification,
    result_persistence_error_classification,
)
from tracecat.executor.service import dispatch_action
from tracecat.logger import logger
from tracecat.observability.otel import platform_span, set_current_span_attributes
from tracecat.observability.sentry import capture_activity_failure
from tracecat.observability.types import PlatformErrorCapture
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorOwner,
)
from tracecat.storage.object import StoredObject, action_key, get_object_storage
from tracecat.temporal.errors import (
    activity_error_boundary,
    build_error_transport_detail,
    extract_error_capture,
    extract_error_classification,
    raise_application_error_from_classification,
)

LEASE_RENEW_INTERVAL_SECONDS: Final = 30
"""Longest gap between lease renewals; well under the 90 s lease expiry."""

ADMISSION_ACK_TIMEOUT_SECONDS: Final = 2.0
"""Ceiling for the admission signal; the run falls back to probe completion."""


async def _heartbeat_loop(
    interval: float,
    task_ref: str,
    action_name: str,
    *,
    send_heartbeats: bool = True,
    renew: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Send periodic heartbeats to Temporal, and renew a lease, until cancelled.

    Runs as a background asyncio task alongside the long-running
    dispatch_action() call. Cancelled by the caller when dispatch completes.
    Each tick heartbeats first, so a slow renewal never delays a heartbeat.
    ``renew`` must not raise.
    """
    elapsed = 0.0
    try:
        while True:
            await asyncio.sleep(interval)
            elapsed += interval
            if send_heartbeats:
                activity.heartbeat(f"{action_name} ({task_ref}): {elapsed:g}s elapsed")
            if renew is not None:
                await renew()
    except asyncio.CancelledError:
        pass


def _lease_id(info: activity.Info) -> str:
    """Attempt-specific lease ID: a retried attempt never reuses a lease."""
    run_id = info.workflow_run_id or info.workflow_id or "no-workflow"
    return f"{run_id}:{info.activity_id}:{info.attempt}"


def _lease_deadline(info: activity.Info) -> datetime:
    """When this attempt must end; a lease never outlives it."""
    if info.start_to_close_timeout is not None:
        return info.started_time + info.start_to_close_timeout
    if info.schedule_to_close_timeout is not None:
        return info.scheduled_time + info.schedule_to_close_timeout
    return datetime.now(UTC) + timedelta(
        seconds=config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT
        + config.TRACECAT__EXECUTOR_ACTIVITY_PLATFORM_TIMEOUT
    )


async def _ack_admission(info: activity.Info) -> None:
    """Tell a waiting run that its probe was admitted. Never raises."""
    if not info.workflow_id:
        return
    try:
        handle = activity.client().get_workflow_handle(
            info.workflow_id, run_id=info.workflow_run_id
        )
        async with asyncio.timeout(ADMISSION_ACK_TIMEOUT_SECONDS):
            await handle.signal(ADMISSION_ACK_SIGNAL, info.activity_id)
    except Exception as e:
        logger.warning(
            "concurrency.ack_failed",
            workflow_id=info.workflow_id,
            activity_id=info.activity_id,
            error=str(e),
        )


def _for_each_width() -> int:
    return max(1, config.TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY)


async def _stop_heartbeat_and_release(
    heartbeat_task: asyncio.Task[None] | None, lease: Lease | None
) -> None:
    """Stop heartbeats and renewal, then release the lease (never raises)."""
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
    if lease is not None:
        await lease.release()


def _source_capture(
    error: Exception, classification: RuntimeErrorClassification, *, action_name: str
) -> PlatformErrorCapture | None:
    """Retain captures made before executor diagnostics severed raw causes."""
    if isinstance(error, ExecutionError):
        capture = error.sentry_capture
        if capture is not None and capture.classification == classification:
            return capture
        return None
    if isinstance(error, LoopExecutionError):
        failures: list[
            tuple[RuntimeErrorClassification, PlatformErrorCapture | None]
        ] = []
        for child in error.loop_errors:
            child_classification = classify_execute_action_error(
                child, action_name=action_name
            )
            capture = _source_capture(
                child, child_classification, action_name=action_name
            )
            failures.append((child_classification, capture))
        return PlatformErrorCapture.for_aggregate(classification, failures)
    return extract_error_capture(error, classification)


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
    ) -> StoredObject | ConcurrencyBusyResult:
        """Execute an action on the ExecutorWorker.

        This activity runs on 'shared-action-queue' and handles:
        - The organization's action concurrency limit (returns
          ``ConcurrencyBusyResult``, having executed nothing, when it is full)
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
        set_current_span_attributes(
            {
                "tracecat.organization.id": role.organization_id,
                "tracecat.workspace.id": role.workspace_id,
                "tracecat.workflow.id": input.run_context.wf_id,
                "tracecat.workflow.execution.id": input.run_context.wf_exec_id,
                "tracecat.action.ref": task.ref,
                "tracecat.action.name": action_name,
                "temporal.activity.attempt": act_attempt,
                "temporal.task_queue": act_info.task_queue,
            }
        )
        log.debug(
            "Execute action activity details",
            task=task,
            attempt=act_attempt,
            retry_policy=task.retry_policy,
            input=input,
        )
        # Take the lease before any effect or S3 read, so a busy answer is cheap.
        # Agent tool calls are not limited yet (ENG-2564 adds their own limit).
        lease: Lease | None = None
        if input.agent_session_id is None and role.organization_id is not None:
            try:
                lease = await gate.acquire(
                    org_id=role.organization_id,
                    resource=ConcurrencyResource.ACTIONS,
                    lease_id=_lease_id(act_info),
                    units=_for_each_width() if task.for_each else 1,
                    deadline=_lease_deadline(act_info),
                )
            except ConcurrencyBusyError as e:
                return ConcurrencyBusyResult(
                    resource=e.resource,
                    in_use=e.in_use,
                    limit=e.limit,
                    attempt=act_attempt,
                )

        heartbeat_interval = config.TRACECAT__ACTIVITY_HEARTBEAT_INTERVAL
        send_heartbeats = heartbeat_interval > 0

        # Run a background heartbeat task for the full activity lifetime
        # (including tenacity backoff sleeps) so Temporal can detect a dead
        # worker without waiting for start_to_close_timeout. The same task
        # renews the lease, so it starts before materialization.
        heartbeat_task: asyncio.Task[None] | None = None
        if send_heartbeats or lease is not None:
            tick = (
                heartbeat_interval if send_heartbeats else LEASE_RENEW_INTERVAL_SECONDS
            )
            if lease is not None:
                tick = min(tick, LEASE_RENEW_INTERVAL_SECONDS)
            if send_heartbeats:
                activity.heartbeat(f"{action_name} ({task.ref}) starting")
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(
                    tick,
                    task.ref,
                    action_name,
                    send_heartbeats=send_heartbeats,
                    renew=lease.renew if lease is not None else None,
                )
            )

        try:
            if input.ack_admission:
                await _ack_admission(act_info)
            with platform_span("tracecat.action.materialize_inputs"):
                materialized_input = input.model_copy(
                    update={
                        "exec_context": await materialize_context(input.exec_context)
                    }
                )
        except BaseException:
            await _stop_heartbeat_and_release(heartbeat_task, lease)
            raise

        try:
            with activity_error_boundary(
                executor_backend_initialization_error_classification
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
                    with platform_span(
                        "tracecat.action.execute",
                        attributes={
                            "tracecat.action.ref": task.ref,
                            "tracecat.action.name": action_name,
                            "tracecat.retry.attempt": (
                                attempt_manager.retry_state.attempt_number
                            ),
                        },
                    ):
                        result = await dispatch_action(
                            backend=backend,
                            input=materialized_input,
                            max_concurrency=(
                                lease.units if lease is not None else None
                            ),
                        )

                    if send_heartbeats:
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
                    with platform_span("tracecat.action.store_result"):
                        with activity_error_boundary(
                            result_persistence_error_classification
                        ):
                            stored = await get_object_storage().store(key, result)
                    return stored
        except Exception as e:
            if is_cancelled_exception(e):
                raise
            classification = classify_execute_action_error(e, action_name=action_name)
            log.bind(
                error_owner=classification.owner,
                error_kind=classification.kind,
                cause_type=classification.cause_type,
            ).log(
                "ERROR"
                if classification.owner is RuntimeErrorOwner.PLATFORM
                else "INFO",
                "Action execution failed",
            )
            # Preserve the Temporal metadata an already-classified failure carried:
            # its own details survive only when it was explicitly classified, and a
            # requested retry delay applies only to a retryable disposition.
            if isinstance(e, ApplicationError):
                detail_type = e.type or e.__class__.__name__
                details = tuple(e.details) if extract_error_classification(e) else ()
                next_retry_delay = e.next_retry_delay
            else:
                detail_type = e.__class__.__name__
                details = ()
                next_retry_delay = None
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=classification.message,
                type=detail_type,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            raise_application_error_from_classification(
                classification,
                build_error_transport_detail(classification, err_info),
                *details,
                capture=(
                    _source_capture(e, classification, action_name=action_name)
                    or capture_activity_failure(e, classification)
                ),
                next_retry_delay=(
                    next_retry_delay
                    if classification.retry_disposition is RetryDisposition.RETRYABLE
                    else None
                ),
            )
        finally:
            await _stop_heartbeat_and_release(heartbeat_task, lease)

        # Unreachable: AsyncRetrying either returns in the loop or raises RetryError
        # (caught by Exception handler above) when retries are exhausted
        raise AssertionError("Unreachable: AsyncRetrying loop must return or raise")
