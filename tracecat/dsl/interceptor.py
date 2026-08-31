from contextlib import suppress
from contextvars import ContextVar
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
    import sentry_sdk as sentry

    from tracecat.dsl.common import get_trigger_type
    from tracecat.logger import logger
    from tracecat.observability.sentry import SentryTag
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
    from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType


class _SentryWrappedWorkflowError(ApplicationError):
    """Mark Sentry's internal wrapper without changing its Temporal payload."""


_ATTRIBUTION_ORIGINAL_ERROR: ContextVar[BaseException | None] = ContextVar(
    "attribution_original_error",
    default=None,
)


def _take_attribution_original_error() -> BaseException | None:
    """Return and clear a history-local error retained for Sentry capture."""
    error = _ATTRIBUTION_ORIGINAL_ERROR.get()
    _ATTRIBUTION_ORIGINAL_ERROR.set(None)
    return error


def _unclassified_retry_disposition(error: BaseException) -> RetryDisposition:
    """Preserve explicit Temporal retryability without guessing for raw errors."""
    for current in iter_error_chain(error, include_implicit_context=False):
        if isinstance(current, _SentryWrappedWorkflowError):
            continue
        if isinstance(current, ApplicationError):
            return (
                RetryDisposition.NON_RETRYABLE
                if current.non_retryable
                else RetryDisposition.RETRYABLE
            )
        if isinstance(current, TemporalTimeoutError):
            return RetryDisposition.RETRYABLE
    return RetryDisposition.NON_RETRYABLE


def _capture_platform_failure(
    error: BaseException,
    classification: RuntimeErrorClassification,
    info: workflow.Info,
    trigger_type: TriggerType,
) -> None:
    """Capture one classified platform event without workflow payload data."""
    with sentry.isolation_scope() as scope:
        scope.fingerprint = [
            "tracecat-runtime-v1",
            classification.kind.value,
            "{{ default }}",
        ]
        scope.set_tag(SentryTag.ERROR_OWNER.value, classification.owner.value)
        scope.set_tag(SentryTag.ERROR_KIND.value, classification.kind.value)
        scope.set_tag(
            SentryTag.ERROR_RETRY_DISPOSITION.value,
            classification.retry_disposition.value,
        )
        scope.set_tag(
            SentryTag.ERROR_CAUSE_TYPE.value,
            classification.cause_type or "unknown",
        )
        scope.set_tag(SentryTag.WORKFLOW_TYPE.value, info.workflow_type)
        scope.set_tag(SentryTag.WORKFLOW_ATTEMPT.value, str(info.attempt))
        scope.set_tag(SentryTag.TRIGGER_TYPE.value, trigger_type.value)
        scope.set_context(
            "tracecat_workflow",
            {
                "run_id": info.run_id,
                "type": info.workflow_type,
                "attempt": info.attempt,
                "trigger_type": trigger_type.value,
            },
        )
        sentry.capture_exception(error)


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
        _capture_platform_failure(error, classification, info, trigger_type)
    else:
        terminal_logger.warning("Terminal user workflow failure")


class _SentryWorkflowInterceptor(WorkflowInboundInterceptor):
    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        try:
            return await super().execute_workflow(input)
        except Exception as error:
            original_error = _take_attribution_original_error()
            if is_cancelled_exception(error) or isinstance(
                error, (TerminatedError, TemporalTimeoutError)
            ):
                raise
            if workflow.unsafe.is_replaying():
                raise

            classifications = extract_error_classifications(
                error,
                include_implicit_context=False,
            )
            if not classifications:
                with suppress(Exception):
                    logger.warning(
                        "Terminal workflow failure reached Sentry without classification",
                        event="workflow_terminal_failure_unclassified",
                        error_type=type(error).__name__,
                    )
                raise

            classification = select_error_classification(classifications)
            try:
                _report_terminal_failure(
                    original_error if original_error is not None else error,
                    classification,
                )
            except Exception as reporting_error:
                with suppress(Exception):
                    logger.warning(
                        "Terminal workflow failure reporting failed",
                        event="workflow_terminal_failure_reporting_failed",
                        error_type=type(reporting_error).__name__,
                        owner=classification.owner.value,
                        kind=classification.kind.value,
                    )
            raise


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

            # Old histories must retain their original terminal command. The
            # interceptor only owns failures from executions that record this
            # patch marker.
            if not workflow.patched(
                WorkflowPatch.RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR
            ):
                raise

            classifications = extract_error_classifications(
                error,
                include_implicit_context=False,
            )
            if classifications:
                classification = select_error_classification(classifications)
                self._stamp_owner(classification)
                raise

            classification = RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
                message="Tracecat encountered an unclassified runtime failure",
                retry_disposition=_unclassified_retry_disposition(error),
                cause=error,
            )
            self._stamp_owner(classification)
            if not workflow.unsafe.is_replaying():
                logger.warning(
                    "Terminal workflow failure reached the attribution fallback",
                    event="runtime_error_attribution_fallback",
                    owner=classification.owner.value,
                    kind=classification.kind.value,
                    retry_disposition=classification.retry_disposition.value,
                    workflow_type=workflow.info().workflow_type,
                    error_type=type(error).__name__,
                )
            _ATTRIBUTION_ORIGINAL_ERROR.set(error)
            raise application_error_from_classification(classification) from None

    @staticmethod
    def _stamp_owner(classification: RuntimeErrorClassification) -> None:
        workflow.upsert_search_attributes(
            [TemporalSearchAttr.ERROR_OWNER.key.value_set(classification.owner.value)]
        )


class SentryInterceptor(Interceptor):
    """Temporal Interceptor class which will report workflow & activity exceptions to Sentry"""

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return _SentryWorkflowInterceptor


class RuntimeErrorAttributionInterceptor(Interceptor):
    """Always-on terminal workflow attribution backstop."""

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return _RuntimeErrorAttributionWorkflowInterceptor


def build_workflow_interceptors(*, sentry_enabled: bool) -> list[Interceptor]:
    """Build the workflow chain with Sentry outside terminal attribution."""
    interceptors: list[Interceptor] = []
    if sentry_enabled:
        interceptors.append(SentryInterceptor())
    interceptors.append(RuntimeErrorAttributionInterceptor())
    return interceptors
