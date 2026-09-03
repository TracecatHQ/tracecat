"""Privacy-bounded Sentry configuration for Tracecat services."""

import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.logger import logger
from tracecat.runtime.errors import RuntimeErrorClassification


@dataclass(frozen=True, slots=True)
class WorkflowFailureEventContext:
    """Privacy-reviewed workflow metadata attached to a platform event."""

    run_id: str
    workflow_type: str
    attempt: int
    trigger_type: str


class SentryTag(StrEnum):
    """Stable, privacy-reviewed Sentry tag keys."""

    SERVICE_NAME = "tracecat.service.name"
    ERROR_OWNER = "tracecat.error.owner"
    ERROR_KIND = "tracecat.error.kind"
    ERROR_RETRY_DISPOSITION = "tracecat.error.retry_disposition"
    ERROR_CAUSE_TYPE = "tracecat.error.cause_type"
    WORKFLOW_TYPE = "temporal.workflow.type"
    WORKFLOW_ATTEMPT = "temporal.workflow.attempt"
    TRIGGER_TYPE = "tracecat.trigger_type"
    API_METHOD = "http.request.method"
    API_ROUTE = "http.route"
    SERVICE_TASK_NAME = "tracecat.service.task.name"


_ALLOWED_TAGS = frozenset(SentryTag)
_ALLOWED_CONTEXT_FIELDS = {
    "runtime": frozenset({"name", "version"}),
    "tracecat_workflow": frozenset({"run_id", "type", "attempt", "trigger_type"}),
    "tracecat_api_request": frozenset({"method", "route"}),
    "tracecat_service_task": frozenset({"name"}),
}


def capture_platform_failure(
    error: BaseException,
    classification: RuntimeErrorClassification,
    context: WorkflowFailureEventContext,
) -> None:
    """Best-effort capture of a classified data-plane platform failure.

    Telemetry must never replace the application failure being reported or
    interrupt a response/callback path if the SDK itself fails.
    """
    try:
        client = sentry_sdk.get_client()
        if not client.is_active() or client.options.get("dsn") is None:
            return

        with sentry_sdk.isolation_scope() as scope:
            scope.fingerprint = [
                "tracecat-runtime-v1",
                classification.kind.value,
                "{{ default }}",
            ]
            scope.set_tag(
                SentryTag.SERVICE_NAME.value,
                config.TRACECAT__SERVICE_NAME,
            )
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
            scope.set_tag(SentryTag.WORKFLOW_TYPE.value, context.workflow_type)
            scope.set_tag(SentryTag.WORKFLOW_ATTEMPT.value, str(context.attempt))
            scope.set_tag(SentryTag.TRIGGER_TYPE.value, context.trigger_type)
            scope.set_context(
                "tracecat_workflow",
                {
                    "run_id": context.run_id,
                    "type": context.workflow_type,
                    "attempt": context.attempt,
                    "trigger_type": context.trigger_type,
                },
            )
            sentry_sdk.capture_exception(error)
    except Exception as reporting_error:
        logger.warning(
            "Failed to capture platform failure in Sentry",
            kind=classification.kind.value,
            reporting_error_type=type(reporting_error).__name__,
        )


def capture_api_background_task_failure(
    error: BaseException,
    *,
    task_name: str,
) -> None:
    """Capture a failed API-owned task that runs outside the ASGI request path."""
    try:
        client = sentry_sdk.get_client()
        if not client.is_active() or client.options.get("dsn") is None:
            return

        with sentry_sdk.isolation_scope() as scope:
            scope.set_tag(
                SentryTag.SERVICE_NAME.value,
                config.TRACECAT__SERVICE_NAME,
            )
            scope.set_tag(SentryTag.SERVICE_TASK_NAME.value, task_name)
            scope.set_context("tracecat_service_task", {"name": task_name})
            sentry_sdk.capture_exception(error)
    except Exception as reporting_error:
        logger.warning(
            "Failed to capture API background task failure in Sentry",
            task=task_name,
            reporting_error_type=type(reporting_error).__name__,
        )


def _enrich_api_request_event(
    event: Event,
    tags: MutableMapping[str, Any],
) -> None:
    """Keep only stable request metadata supplied by the framework integration."""
    request = event.get("request")
    if not isinstance(request, MutableMapping):
        return
    method = request.get("method")
    method = method if isinstance(method, str) else "UNKNOWN"
    transaction = event.get("transaction")
    transaction_info = cast(
        MutableMapping[str, Any], event.get("transaction_info") or {}
    )
    route = (
        transaction
        if isinstance(transaction, str) and transaction_info.get("source") == "route"
        else "unmatched"
    )

    tags[SentryTag.SERVICE_NAME.value] = "api"
    tags[SentryTag.API_METHOD.value] = method
    tags[SentryTag.API_ROUTE.value] = route
    contexts = cast(MutableMapping[str, Any], event.get("contexts") or {})
    contexts["tracecat_api_request"] = {"method": method, "route": route}
    event["contexts"] = dict(contexts)


def _sanitize_event(event: Event, *, safe_value: str) -> Event:
    """Reduce a Sentry event to the shared privacy-reviewed schema."""
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    event["tags"] = {key: value for key, value in tags.items() if key in _ALLOWED_TAGS}
    contexts = cast(MutableMapping[str, Any], event.get("contexts") or {})
    sanitized_contexts: dict[str, dict[str, Any]] = {}
    for context_name, allowed_fields in _ALLOWED_CONTEXT_FIELDS.items():
        context = contexts.get(context_name)
        if not isinstance(context, MutableMapping):
            continue
        sanitized_contexts[context_name] = {
            key: value for key, value in context.items() if key in allowed_fields
        }
    event["contexts"] = sanitized_contexts
    for field in ("breadcrumbs", "extra", "request", "user"):
        event.pop(field, None)

    exception = cast(MutableMapping[str, Any], event.get("exception") or {})
    values = exception.get("values")
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, MutableMapping):
                continue
            value["value"] = safe_value
            value.pop("raw_stacktrace", None)
            mechanism = value.get("mechanism")
            if isinstance(mechanism, MutableMapping):
                mechanism.pop("data", None)

    return event


def _sanitize_platform_event(event: Event, hint: Hint) -> Event | None:
    """Drop non-platform events and strip payload-bearing Sentry fields."""
    del hint
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    if tags.get(SentryTag.ERROR_OWNER) != "platform":
        return None
    kind = tags.get(SentryTag.ERROR_KIND, "unclassified")
    return _sanitize_event(event, safe_value=f"Tracecat platform failure ({kind})")


def _sanitize_api_event(event: Event, hint: Hint) -> Event:
    """Strip API events to stable, privacy-reviewed metadata."""
    del hint
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    tags.setdefault(SentryTag.SERVICE_NAME.value, "api")
    _enrich_api_request_event(event, tags)
    event["tags"] = dict(tags)
    return _sanitize_event(event, safe_value="Tracecat API failure")


def initialize_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    service_name: str,
    enable_fastapi_integration: bool = False,
    transport: Transport | None = None,
) -> None:
    """Initialize the privacy-bounded Sentry client for a service process."""
    integrations: list[Integration] = [AtexitIntegration()]
    if enable_fastapi_integration:
        integrations.extend(
            [
                StarletteIntegration(
                    transaction_style="url",
                    failed_request_status_codes=set(),
                    middleware_spans=False,
                ),
                FastApiIntegration(
                    transaction_style="url",
                    failed_request_status_codes=set(),
                    middleware_spans=False,
                ),
            ]
        )
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        server_name=service_name,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=integrations,
        send_default_pii=False,
        include_local_variables=False,
        max_breadcrumbs=0,
        max_request_body_size="never",
        before_send=(
            _sanitize_api_event
            if enable_fastapi_integration
            else _sanitize_platform_event
        ),
        transport=transport,
    )


def initialize_sentry_from_environment(
    *,
    enable_fastapi_integration: bool = False,
) -> None:
    """Initialize Sentry from process configuration when a DSN is present."""
    if not (dsn := os.environ.get("SENTRY_DSN")):
        return

    app_env = config.TRACECAT__APP_ENV
    temporal_namespace = config.TEMPORAL__CLUSTER_NAMESPACE
    environment = (
        config.SENTRY_ENVIRONMENT_OVERRIDE or f"{app_env}-{temporal_namespace}"
    )
    logger.info(
        "Initializing Sentry",
        environment=environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )
    initialize_sentry(
        dsn=dsn,
        environment=environment,
        release=f"tracecat@{APP_VERSION}",
        service_name=config.TRACECAT__SERVICE_NAME,
        enable_fastapi_integration=enable_fastapi_integration,
    )
    logger.info(
        "Sentry initialized",
        environment=environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )
