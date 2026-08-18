"""Temporal activities for the ExecutorWorker.

These activities run on the 'shared-action-queue' and handle action execution
dispatched from DSL workflows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

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
from tracecat.dsl.schemas import RunActionInput
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import RateLimitExceeded
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.error_policy import (
    classify_execute_action_error,
    executor_backend_initialization_error_classification,
    result_persistence_error_classification,
)
from tracecat.executor.service import dispatch_action
from tracecat.logger import logger
from tracecat.observability.otel import platform_span, set_current_span_attributes
from tracecat.runtime.errors import RetryDisposition, RuntimeErrorOwner
from tracecat.storage.object import StoredObject, action_key, get_object_storage
from tracecat.temporal.errors import (
    activity_error_boundary,
    build_error_transport_detail,
    extract_error_classification,
    raise_application_error_from_classification,
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
        set_current_span_attributes(
            {
                "tracecat.organization.id": (
                    str(role.organization_id) if role.organization_id else None
                ),
                "tracecat.workspace.id": (
                    str(role.workspace_id) if role.workspace_id else None
                ),
                "tracecat.workflow.id": str(input.run_context.wf_id),
                "tracecat.workflow.execution.id": str(input.run_context.wf_exec_id),
                "tracecat.trigger.type": input.run_context.trigger_type,
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
        with platform_span("tracecat.action.materialize_inputs"):
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
                    with platform_span("tracecat.action.store_result"):
                        with activity_error_boundary(
                            result_persistence_error_classification
                        ):
                            stored = await get_object_storage().store(key, result)
                    return stored
        except Exception as e:
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
                next_retry_delay=(
                    next_retry_delay
                    if classification.retry_disposition is RetryDisposition.RETRYABLE
                    else None
                ),
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
