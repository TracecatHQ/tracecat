from dataclasses import is_dataclass
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, FailureError
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    ExecuteWorkflowInput,
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

with workflow.unsafe.imports_passed_through():
    import sentry_sdk as sentry

    from tracecat.contexts import ctx_role
    from tracecat.dsl.common import DSLRunArgs, get_trigger_type
    from tracecat.exceptions import TracecatException
    from tracecat.logger import logger
    from tracecat.workflow.executions.enums import TriggerType

_USER_FACING_EXCEPTION_CLASSES = (TracecatException,)

_USER_FACING_APPLICATION_ERROR_TYPES = frozenset(
    {
        "EntitlementRequired",
        "ExecutionError",
        "LoopExecutionError",
        "RegistryActionValidationError",
        "ScopeDeniedError",
        "TracecatException",
        "TracecatExpressionError",
        "TracecatValidationError",
        "UserError",
    }
)


def _set_common_workflow_tags(info: workflow.Info | activity.Info) -> None:
    sentry.set_tag("temporal.workflow.type", info.workflow_type)
    sentry.set_tag("temporal.workflow.id", info.workflow_id)


def _set_fingerprint(
    scope: sentry.Scope,
    input: ExecuteWorkflowInput,
    info: workflow.Info,
    trigger_type: TriggerType,
) -> None:
    if len(input.args) > 0 and isinstance(input.args[0], DSLRunArgs):
        arg = input.args[0]
        wf_id = arg.wf_id.short()
        logger.info("Using workflow ID for fingerprint", workflow_id=wf_id)
        scope.fingerprint = [wf_id, trigger_type.value]
        sentry.set_tag("tracecat.workflow_id", wf_id)

        # Set additional tags for the workflow from its DSLRunArgs
        if parent_run_context := arg.parent_run_context:
            sentry.set_tag("tracecat.parent.workflow_id", str(parent_run_context.wf_id))
            sentry.set_tag(
                "tracecat.parent.workflow_exec_id", parent_run_context.wf_exec_id
            )
        if dsl := arg.dsl:
            sentry.set_tag("tracecat.dsl.title", dsl.title)
            sentry.set_tag("tracecat.dsl.description", dsl.description)
            sentry.set_tag("tracecat.dsl.error_handler", dsl.error_handler)
            sentry.set_tag("tracecat.dsl.config.timeout", dsl.config.timeout)
    else:
        logger.warning(
            "Couldn't find DSLRunArgs, deriving workflow ID from execution ID for fingerprint"
        )
        wf_exec_id = info.workflow_id
        try:
            scope.fingerprint = [wf_exec_id.split("/")[0], trigger_type.value]
        except Exception as e:
            logger.warning(
                "Failed to derive workflow ID for fingerprint, falling back to execution ID",
                error=str(e),
            )
            scope.fingerprint = [wf_exec_id, trigger_type.value]


class _SentryWorkflowInterceptor(WorkflowInboundInterceptor):
    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        # https://docs.sentry.io/platforms/python/troubleshooting/#addressing-concurrency-issues
        with sentry.isolation_scope() as scope:
            sentry.set_tag("temporal.execution_type", "workflow")
            sentry.set_tag(
                "module", input.run_fn.__module__ + "." + input.run_fn.__qualname__
            )
            info = workflow.info()
            _set_common_workflow_tags(info)
            sentry.set_user({"id": info.namespace})
            sentry.set_tag("temporal.workflow.task_queue", info.task_queue)
            sentry.set_tag("temporal.workflow.namespace", info.namespace)
            sentry.set_tag("temporal.workflow.run_id", info.run_id)
            trigger_type = get_trigger_type(info)
            if (role := ctx_role.get()) and role.workspace_id:
                sentry.set_tag("tracecat.workspace_id", str(role.workspace_id))
            sentry.set_tag("tracecat.trigger_type", trigger_type.value)
            # Fingerprint to each workflow ID
            _set_fingerprint(scope, input, info, trigger_type)
            try:
                return await super().execute_workflow(input)
            except ApplicationError as e:
                logger.warning("Caught application level workflow error", error=str(e))
                # We want to raise the error so that the workflow can be retried
                # We want to log the error if this is a scheduled workflow
                # Get the temporal search attribute for TracecatTriggerType
                match trigger_type:
                    case TriggerType.SCHEDULED:
                        logger.info("Reporting scheduled workflow error")
                        if not workflow.unsafe.is_replaying():
                            # NOTE: We log here instead of capturing the exception because of metaclass issues with ApplicationError
                            # Related issue: https://temporalio.slack.com/archives/CTT84RS0P/p1720730740608279?thread_ts=1720727238.727909&cid=CTT84RS0P
                            logger.error("Scheduled workflow error", error=str(e))
                    case TriggerType.WEBHOOK:
                        logger.info("Reporting webhook workflow error")
                        if not workflow.unsafe.is_replaying():
                            # See note above
                            logger.error("Webhook workflow error", error=str(e))
                    case _:
                        logger.info("Not a scheduled workflow, skipping reporting")
                raise e
            except Exception as e:
                logger.warning("Caught platform level workflow error", error=str(e))
                if len(input.args) >= 1:
                    sentry.set_context(
                        "temporal.workflow.input",
                        _workflow_input_context(input.args[0]),
                    )
                sentry.set_context("temporal.workflow.info", workflow.info().__dict__)

                if (
                    not workflow.unsafe.is_replaying()
                    and _should_capture_workflow_exception(e)
                ):
                    sentry.capture_exception(e)
                    logger.error(
                        "Unexpected workflow error, likely a platform issue",
                        error=str(e),
                    )
                logger.debug(
                    "Reraising exception as ApplicationError to fail workflow gracefully"
                )
                raise ApplicationError(str(e)) from e


class _SentryActivityInboundInterceptor(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        with sentry.isolation_scope():
            info = activity.info()
            sentry.set_tag("temporal.execution_type", "activity")
            sentry.set_tag("module", input.fn.__module__ + "." + input.fn.__qualname__)
            _set_common_workflow_tags(info)
            sentry.set_user({"id": info.workflow_namespace})
            sentry.set_tag("temporal.activity.type", info.activity_type)
            sentry.set_tag("temporal.activity.id", info.activity_id)
            sentry.set_tag("temporal.activity.attempt", info.attempt)
            sentry.set_tag("temporal.activity.task_queue", info.task_queue)
            sentry.set_tag("temporal.workflow.namespace", info.workflow_namespace)
            sentry.set_tag("temporal.workflow.run_id", info.workflow_run_id)
            if (role := ctx_role.get()) and role.workspace_id:
                sentry.set_tag("tracecat.workspace_id", str(role.workspace_id))
            try:
                return await super().execute_activity(input)
            except Exception as e:
                if _should_capture_activity_exception(e, info):
                    sentry.set_context(
                        "temporal.activity.info",
                        _activity_info_context(info),
                    )
                    sentry.capture_exception(e)
                raise


def _should_capture_activity_exception(
    exc: Exception, info: activity.Info | None = None
) -> bool:
    if not _should_capture_temporal_exception(exc):
        return False
    if info is None or _is_non_retryable_temporal_exception(exc):
        return True
    return _activity_retry_exhausted(info)


def _should_capture_workflow_exception(exc: Exception) -> bool:
    return _should_capture_temporal_exception(exc)


def _should_capture_temporal_exception(exc: Exception) -> bool:
    if isinstance(exc, _USER_FACING_EXCEPTION_CLASSES):
        return False
    if isinstance(exc, ApplicationError):
        return (
            exc.type is not None
            and exc.type not in _USER_FACING_APPLICATION_ERROR_TYPES
        )
    if isinstance(exc, FailureError) and isinstance(exc.cause, Exception):
        return _should_capture_temporal_exception(exc.cause)
    return True


def _is_non_retryable_temporal_exception(exc: Exception) -> bool:
    if isinstance(exc, ApplicationError):
        return exc.non_retryable
    if isinstance(exc, FailureError) and isinstance(exc.cause, Exception):
        return _is_non_retryable_temporal_exception(exc.cause)
    return False


def _activity_retry_exhausted(info: activity.Info) -> bool:
    retry_policy = info.retry_policy
    if retry_policy is None:
        return True
    maximum_attempts = retry_policy.maximum_attempts
    return maximum_attempts > 0 and info.attempt >= maximum_attempts


def _workflow_input_context(arg: Any) -> dict[str, Any]:
    context: dict[str, Any] = {"type": type(arg).__name__}
    if isinstance(arg, DSLRunArgs):
        context["wf_id"] = arg.wf_id.short()
        context["execution_type"] = arg.execution_type.value
        context["timeout_seconds"] = arg.timeout.total_seconds()
        if arg.schedule_id:
            context["schedule_id"] = str(arg.schedule_id)
        if arg.time_anchor:
            context["time_anchor"] = arg.time_anchor.isoformat()
        if parent_run_context := arg.parent_run_context:
            context["parent_run_context"] = {
                "wf_id": str(parent_run_context.wf_id),
                "wf_exec_id": parent_run_context.wf_exec_id,
            }
    elif is_dataclass(arg) and not isinstance(arg, type):
        context["dataclass_type"] = type(arg).__qualname__
    return context


def _activity_info_context(info: activity.Info) -> dict[str, Any]:
    return {
        "activity_id": info.activity_id,
        "activity_type": info.activity_type,
        "attempt": info.attempt,
        "is_local": info.is_local,
        "task_queue": info.task_queue,
        "workflow_id": info.workflow_id,
        "workflow_namespace": info.workflow_namespace,
        "workflow_run_id": info.workflow_run_id,
        "workflow_type": info.workflow_type,
        "retry_policy": _retry_policy_context(info.retry_policy),
    }


def _retry_policy_context(retry_policy: RetryPolicy | None) -> dict[str, Any] | None:
    if retry_policy is None:
        return None
    return {
        "initial_interval_seconds": retry_policy.initial_interval.total_seconds(),
        "backoff_coefficient": retry_policy.backoff_coefficient,
        "maximum_interval_seconds": retry_policy.maximum_interval.total_seconds()
        if retry_policy.maximum_interval
        else None,
        "maximum_attempts": retry_policy.maximum_attempts,
        "non_retryable_error_types": retry_policy.non_retryable_error_types,
    }


class SentryInterceptor(Interceptor):
    """Temporal Interceptor class which will report workflow & activity exceptions to Sentry"""

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _SentryActivityInboundInterceptor(next)

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return _SentryWorkflowInterceptor
