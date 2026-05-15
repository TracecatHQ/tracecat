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
from tracecat.exceptions import (
    EntitlementRequired,
    ExecutionError,
    LoopExecutionError,
    RateLimitExceeded,
    ScopeDeniedError,
)
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.errors import ActionRuntimeError
from tracecat.executor.service import dispatch_action
from tracecat.logger import logger
from tracecat.runtime.errors import RuntimeErrorPhase
from tracecat.storage.object import StoredObject, action_key, get_object_storage
from tracecat.temporal.errors import extract_runtime_error


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
        - Sandboxed pool execution

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
        try:
            materialized_input = input.model_copy(
                update={"exec_context": await materialize_context(input.exec_context)}
            )
        except Exception as e:
            kind = e.__class__.__name__
            raw_msg = f"Failed to materialize action context:\n{e}"
            log.error(raw_msg)
            raise ActionRuntimeError.known_unknown(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.materialize_context.failed",
                message=raw_msg,
                error_type=kind,
                phase=RuntimeErrorPhase.PREPARE,
                root=e,
            ) from e

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
                    try:
                        stored = await get_object_storage().store(key, result)
                    except Exception as e:
                        kind = e.__class__.__name__
                        raw_msg = f"Failed to store action result:\n{e}"
                        log.error(raw_msg)
                        raise ActionRuntimeError.infra(
                            ref=task.ref,
                            stream_id=input.stream_id,
                            attempt=act_attempt,
                            code="executor.result_storage.failed",
                            message=raw_msg,
                            error_type=kind,
                            phase=RuntimeErrorPhase.COLLECT,
                            root=e,
                        ) from e
                    return stored
        except ScopeDeniedError as e:
            # ScopeDeniedError from dispatch_action (user lacks action permission)
            kind = e.__class__.__name__
            msg = f"Permission denied: missing scope(s) {e.missing_scopes} to execute action '{action_name}'"
            log.warning(
                "Action scope denied",
                action=action_name,
                required_scopes=e.required_scopes,
                missing_scopes=e.missing_scopes,
            )
            # Non-retryable: retrying won't help if user lacks permission
            raise ActionRuntimeError.user(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.scope_denied",
                message=msg,
                error_type=kind,
                phase=RuntimeErrorPhase.USER_CODE,
                root=e,
            ) from e
        except EntitlementRequired as e:
            # Entitlement errors are user-facing and non-retryable
            kind = e.__class__.__name__
            msg = str(e)
            log.warning("Action entitlement denied", action=action_name, error=msg)
            raise ActionRuntimeError.user(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.entitlement_required",
                message=msg,
                error_type=kind,
                phase=RuntimeErrorPhase.USER_CODE,
                root=e,
            ) from e
        except ExecutionError as e:
            # ExecutionError from dispatch_action (single action failure)
            kind = e.__class__.__name__
            msg = str(e)
            log.info("Execution error", error=msg, info=e.info)
            raise ActionRuntimeError.user(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.execution_error",
                message=msg,
                error_type=kind,
                phase=RuntimeErrorPhase.USER_CODE,
                retryable=True,
                root=e,
                non_retryable=False,
            ) from e
        except LoopExecutionError as e:
            # LoopExecutionError from dispatch_action (for_each loop failure)
            kind = e.__class__.__name__
            msg = str(e)
            log.info("Loop execution error", error=msg, loop_errors=e.loop_errors)
            raise ActionRuntimeError.user(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.loop_execution_error",
                message=msg,
                error_type=kind,
                phase=RuntimeErrorPhase.USER_CODE,
                retryable=True,
                root=e,
                non_retryable=False,
            ) from e
        except ApplicationError as e:
            # Pass through ApplicationError
            log.error("ApplicationError occurred", error=e)
            envelope = extract_runtime_error(e, ref=task.ref)
            if envelope is not None:
                raise ActionRuntimeError.existing(
                    envelope,
                    ref=task.ref,
                    stream_id=input.stream_id,
                    attempt=act_attempt,
                    error_type=e.type or e.__class__.__name__,
                    non_retryable=e.non_retryable,
                ) from e
            raise ActionRuntimeError.platform(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="executor.application_error",
                message=str(e),
                error_type=e.type or e.__class__.__name__,
                phase=RuntimeErrorPhase.USER_CODE,
                retryable=not e.non_retryable,
                root=e,
                non_retryable=e.non_retryable,
            ) from e
        except Exception as e:
            # Unexpected errors - non-retryable
            kind = e.__class__.__name__
            raw_msg = f"Unexpected {kind} occurred:\n{e}"
            log.error(raw_msg)

            raise ActionRuntimeError.known_unknown(
                ref=task.ref,
                stream_id=input.stream_id,
                attempt=act_attempt,
                code="runtime.unknown_platform_error",
                message=raw_msg,
                error_type=kind,
                phase=RuntimeErrorPhase.USER_CODE,
                root=e,
            ) from e
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
