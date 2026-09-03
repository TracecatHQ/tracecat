"""Privacy-bounded Sentry configuration for explicit platform error capture."""

import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations.atexit import AtexitIntegration
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


@dataclass(frozen=True, slots=True)
class ApiRequestFailureEventContext:
    """Privacy-reviewed API request metadata attached to a platform event."""

    method: str
    route: str


@dataclass(frozen=True, slots=True)
class ServiceTaskFailureEventContext:
    """Privacy-reviewed service task metadata attached to a platform event."""

    task_name: str


type PlatformFailureEventContext = (
    WorkflowFailureEventContext
    | ApiRequestFailureEventContext
    | ServiceTaskFailureEventContext
)


class SentryTag(StrEnum):
    """Stable, privacy-reviewed tag keys for platform failure events."""

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
    context: PlatformFailureEventContext,
) -> None:
    """Best-effort capture with allowlisted service context.

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
            match context:
                case WorkflowFailureEventContext():
                    scope.set_tag(SentryTag.WORKFLOW_TYPE.value, context.workflow_type)
                    scope.set_tag(
                        SentryTag.WORKFLOW_ATTEMPT.value, str(context.attempt)
                    )
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
                case ApiRequestFailureEventContext():
                    scope.set_tag(SentryTag.API_METHOD.value, context.method)
                    scope.set_tag(SentryTag.API_ROUTE.value, context.route)
                    scope.set_context(
                        "tracecat_api_request",
                        {"method": context.method, "route": context.route},
                    )
                case ServiceTaskFailureEventContext():
                    scope.set_tag(SentryTag.SERVICE_TASK_NAME.value, context.task_name)
                    scope.set_context(
                        "tracecat_service_task",
                        {"name": context.task_name},
                    )
            sentry_sdk.capture_exception(error)
    except Exception as reporting_error:
        logger.warning(
            "Failed to capture platform failure in Sentry",
            kind=classification.kind.value,
            reporting_error_type=type(reporting_error).__name__,
        )


def _sanitize_platform_event(event: Event, hint: Hint) -> Event | None:
    """Drop non-platform events and strip payload-bearing Sentry fields."""
    # Sentry's third-party Event schema is intentionally open-ended, so these
    # mappings retain Any while we reduce them to a fixed allowlist.
    del hint
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    if tags.get(SentryTag.ERROR_OWNER) != "platform":
        return None

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

    safe_value = (
        f"Tracecat platform failure ({tags.get(SentryTag.ERROR_KIND, 'unclassified')})"
    )
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


def initialize_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    service_name: str,
    transport: Transport | None = None,
) -> None:
    """Initialize an explicit-capture-only Sentry client for a worker process."""
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        server_name=service_name,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[AtexitIntegration()],
        send_default_pii=False,
        include_local_variables=False,
        max_breadcrumbs=0,
        before_send=_sanitize_platform_event,
        transport=transport,
    )


def initialize_sentry_from_environment() -> None:
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
    )
    logger.info(
        "Sentry initialized",
        environment=environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )
