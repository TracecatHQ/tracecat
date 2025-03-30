from dataclasses import asdict, is_dataclass
from typing import Any

from temporalio import activity, workflow
from temporalio.common import SearchAttributeKey
from temporalio.exceptions import ApplicationError
from temporalio.worker import (
    ExecuteWorkflowInput,
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

with workflow.unsafe.imports_passed_through():
    import sentry_sdk as sentry
    from pydantic import BaseModel

    from tracecat.contexts import ctx_role
    from tracecat.logger import logger
    from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType


def _set_common_workflow_tags(info: workflow.Info | activity.Info):
    sentry.set_tag("temporal.workflow.type", info.workflow_type)
    sentry.set_tag("temporal.workflow.id", info.workflow_id)


class _SentryWorkflowInterceptor(WorkflowInboundInterceptor):
    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        # https://docs.sentry.io/platforms/python/troubleshooting/#addressing-concurrency-issues
        with sentry.isolation_scope() as scope:
            sentry.set_tag("temporal.execution_type", "workflow")
            sentry.set_tag(
                "module", input.run_fn.__module__ + "." + input.run_fn.__qualname__
            )
            workflow_info = workflow.info()
            _set_common_workflow_tags(workflow_info)
            sentry.set_tag("temporal.workflow.task_queue", workflow_info.task_queue)
            sentry.set_tag("temporal.workflow.namespace", workflow_info.namespace)
            sentry.set_tag("temporal.workflow.run_id", workflow_info.run_id)
            search_attributes = workflow_info.typed_search_attributes
            trigger_type = search_attributes.get(
                SearchAttributeKey.for_keyword(TemporalSearchAttr.TRIGGER_TYPE.value)
            )
            if (role := ctx_role.get()) and role.workspace_id:
                sentry.set_tag("tracecat.workspace_id", str(role.workspace_id))
            scope.fingerprint = [workflow_info.workflow_id]
            try:
                return await super().execute_workflow(input)
            except ApplicationError as e:
                logger.warning("Caught application level workflow error", error=str(e))
                # We want to raise the error so that the workflow can be retried
                # We want to log the error if this is a scheduled workflow
                # Get the temporal search attribute for TracecatTriggerType
                if trigger_type == TriggerType.SCHEDULED:
                    logger.info("Reporting scheduled workflow error")
                    if not workflow.unsafe.is_replaying():
                        # NOTE: We log here instead of capturing the exception because of metaclass issues with ApplicationError
                        # Related issue: https://temporalio.slack.com/archives/CTT84RS0P/p1720730740608279?thread_ts=1720727238.727909&cid=CTT84RS0P
                        logger.error("Scheduled workflow error", error=str(e))
                else:
                    logger.info("Not a scheduled workflow, skipping reporting")
                raise e
            except Exception as e:
                logger.warning("Caught platform level workflow error", error=str(e))
                if len(input.args) >= 1:
                    arg = input.args[0]
                    if is_dataclass(arg) and not isinstance(arg, type):
                        sentry.set_context("temporal.workflow.input", asdict(arg))
                    elif isinstance(arg, BaseModel):
                        sentry.set_context("temporal.workflow.input", arg.model_dump())
                sentry.set_context("temporal.workflow.info", workflow.info().__dict__)

                if not workflow.unsafe.is_replaying():
                    # NOTE: We log here instead of capturing the exception because of metaclass issues with ApplicationError
                    # Related issue: https://temporalio.slack.com/archives/CTT84RS0P/p1720730740608279?thread_ts=1720727238.727909&cid=CTT84RS0P
                    logger.error(
                        "Unexpected workflow error, likely a platform issue",
                        error=str(e),
                    )
                logger.debug(
                    "Reraising exception as ApplicationError to fail workflow gracefully"
                )
                raise ApplicationError(str(e)) from e


class SentryInterceptor(Interceptor):
    """Temporal Interceptor class which will report workflow & activity exceptions to Sentry"""

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return _SentryWorkflowInterceptor
