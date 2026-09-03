from contextlib import suppress
from typing import Any

from temporalio import workflow
from temporalio.exceptions import (
    ApplicationError,
    TerminatedError,
    is_cancelled_exception,
)
from temporalio.exceptions import TimeoutError as TemporalTimeoutError
from temporalio.worker import (
    ExecuteWorkflowInput,
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

with workflow.unsafe.imports_passed_through():
    from tracecat.dsl.common import get_trigger_type
    from tracecat.logger import logger
    from tracecat.observability.sentry import (
        WorkflowFailureEventContext,
        capture_platform_failure,
    )
    from tracecat.runtime.errors import (
        RetryDisposition,
        RuntimeErrorClassification,
        RuntimeErrorKind,
        RuntimeErrorOwner,
        select_error_classification,
    )
    from tracecat.temporal.errors import (
        application_error_from_classification,
        extract_error_classifications,
        iter_error_chain,
    )
    from tracecat.temporal.patches import WorkflowPatch
    from tracecat.workflow.executions.enums import TemporalSearchAttr


def _unclassified_retry_disposition(error: BaseException) -> RetryDisposition:
    """Preserve explicit Temporal retryability without guessing for raw errors."""
    for current in iter_error_chain(error, include_implicit_context=False):
        if isinstance(current, ApplicationError):
            return (
                RetryDisposition.NON_RETRYABLE
                if current.non_retryable
                else RetryDisposition.RETRYABLE
            )
        if isinstance(current, TemporalTimeoutError):
            return RetryDisposition.RETRYABLE
    return RetryDisposition.NON_RETRYABLE


def _report_terminal_failure(
    error: BaseException,
    classification: RuntimeErrorClassification,
) -> None:
    """Emit the bounded terminal log and optional platform Sentry event."""
    info = workflow.info()
    trigger_type = get_trigger_type(info)
    terminal_logger = logger.bind(
        event="workflow_terminal_failure",
        owner=classification.owner.value,
        kind=classification.kind.value,
        retry_disposition=classification.retry_disposition.value,
        cause_type=classification.cause_type,
        workflow_type=info.workflow_type,
        workflow_run_id=info.run_id,
        workflow_attempt=info.attempt,
        trigger_type=trigger_type.value,
    )
    if classification.owner is RuntimeErrorOwner.PLATFORM:
        terminal_logger.error("Terminal platform workflow failure")
        capture_platform_failure(
            error,
            classification,
            WorkflowFailureEventContext(
                run_id=info.run_id,
                workflow_type=info.workflow_type,
                attempt=info.attempt,
                trigger_type=trigger_type.value,
            ),
        )
    else:
        terminal_logger.warning("Terminal user workflow failure")


class _RuntimeErrorAttributionWorkflowInterceptor(WorkflowInboundInterceptor):
    """Guarantee that every escaping application failure has an owner.

    Specific runtime boundaries remain responsible for assigning precise kinds,
    messages, and retry dispositions. This interceptor is the last-resort signal
    that one of those classifiers is missing: an unknown terminal failure is
    intentionally surfaced as ``platform/runtime.unclassified``.
    """

    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        try:
            return await super().execute_workflow(input)
        except Exception as error:
            if is_cancelled_exception(error) or isinstance(
                error, (TerminatedError, TemporalTimeoutError)
            ):
                raise

            classifications = extract_error_classifications(
                error,
                include_implicit_context=False,
            )

            if not workflow.patched(
                WorkflowPatch.RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR
            ):
                raise

            if classifications:
                classification = select_error_classification(classifications)
                self._stamp_owner(classification)
                self._report_failure(error, classification)
                raise

            classification = RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
                message="Tracecat encountered an unclassified runtime failure",
                retry_disposition=_unclassified_retry_disposition(error),
                cause=error,
            )
            self._stamp_owner(classification)
            self._report_failure(error, classification)
            raise application_error_from_classification(classification) from None

    @staticmethod
    def _report_failure(
        error: BaseException,
        classification: RuntimeErrorClassification,
    ) -> None:
        if workflow.unsafe.is_replaying():
            return
        try:
            _report_terminal_failure(error, classification)
        except Exception as reporting_error:
            with suppress(Exception):
                logger.warning(
                    "Terminal workflow failure reporting failed",
                    event="workflow_terminal_failure_reporting_failed",
                    error_type=type(reporting_error).__name__,
                    owner=classification.owner.value,
                    kind=classification.kind.value,
                )

    @staticmethod
    def _stamp_owner(classification: RuntimeErrorClassification) -> None:
        workflow.upsert_search_attributes(
            [TemporalSearchAttr.ERROR_OWNER.key.value_set(classification.owner.value)]
        )


class RuntimeErrorAttributionInterceptor(Interceptor):
    """Always-on terminal workflow attribution backstop."""

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return _RuntimeErrorAttributionWorkflowInterceptor
