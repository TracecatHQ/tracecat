"""Privacy-bounded Sentry configuration for Tracecat services."""

import os
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Any, Literal, Protocol, cast

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


_WORKER_ALLOWED_TAGS = frozenset(
    {
        SentryTag.SERVICE_NAME.value,
        SentryTag.ERROR_OWNER.value,
        SentryTag.ERROR_KIND.value,
        SentryTag.ERROR_RETRY_DISPOSITION.value,
        SentryTag.ERROR_CAUSE_TYPE.value,
        SentryTag.WORKFLOW_TYPE.value,
        SentryTag.WORKFLOW_ATTEMPT.value,
        SentryTag.TRIGGER_TYPE.value,
    }
)
_API_ALLOWED_TAGS = frozenset(
    {
        SentryTag.SERVICE_NAME.value,
        SentryTag.API_METHOD.value,
        SentryTag.API_ROUTE.value,
        SentryTag.SERVICE_TASK_NAME.value,
    }
)
_WORKER_ALLOWED_CONTEXT_FIELDS = {
    "runtime": frozenset({"name", "version"}),
    "tracecat_workflow": frozenset({"run_id", "type", "attempt", "trigger_type"}),
}
_API_ALLOWED_CONTEXT_FIELDS = {
    "runtime": frozenset({"name", "version"}),
    "tracecat_api_request": frozenset({"method", "route"}),
    "tracecat_service_task": frozenset({"name"}),
}
_SAFE_HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
_BASE_ALLOWED_EVENT_FIELDS = frozenset(
    {
        "environment",
        "event_id",
        "level",
        "platform",
        "release",
        "server_name",
        "timestamp",
    }
)
_WORKER_ALLOWED_EVENT_FIELDS = _BASE_ALLOWED_EVENT_FIELDS | {"fingerprint"}
_API_ALLOWED_EVENT_FIELDS = _BASE_ALLOWED_EVENT_FIELDS | {"transaction"}
_ALLOWED_EXCEPTION_STRING_FIELDS = frozenset({"module", "type"})
_ALLOWED_MECHANISM_STRING_FIELDS = frozenset({"type"})
_ALLOWED_MECHANISM_BOOLEAN_FIELDS = frozenset(
    {"handled", "is_exception_group", "synthetic"}
)
_ALLOWED_MECHANISM_INTEGER_FIELDS = frozenset({"exception_id", "parent_id"})
_ALLOWED_FRAME_STRING_FIELDS = frozenset(
    {"filename", "function", "module", "package", "platform"}
)
_ALLOWED_FRAME_INTEGER_FIELDS = frozenset({"colno", "instruction_offset", "lineno"})
_ALLOWED_FRAME_BOOLEAN_FIELDS = frozenset({"in_app"})

type _BeforeSend = Callable[[Event, Hint], Event | None]
type _ContextFieldPolicy = Mapping[str, frozenset[str]]


class _SentryInitializer(Protocol):
    def __call__(
        self,
        *,
        dsn: str,
        environment: str,
        release: str,
        transport: Transport | None = None,
    ) -> None: ...


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
        event.pop("transaction", None)
        event.pop("transaction_info", None)
        return
    raw_method = request.get("method")
    method = raw_method.upper() if isinstance(raw_method, str) else "UNKNOWN"
    if method not in _SAFE_HTTP_METHODS:
        method = "UNKNOWN"
    transaction = event.get("transaction")
    transaction_info = cast(
        MutableMapping[str, Any], event.get("transaction_info") or {}
    )
    route = (
        transaction
        if isinstance(transaction, str) and transaction_info.get("source") == "route"
        else "unmatched"
    )

    # Sentry uses the raw URL as the transaction name before route resolution.
    # Replace it even though the request field is removed below: webhook URLs
    # contain a credential in the path.
    event["transaction"] = route
    event.pop("transaction_info", None)

    tags[SentryTag.API_METHOD.value] = method
    tags[SentryTag.API_ROUTE.value] = route
    contexts = cast(MutableMapping[str, Any], event.get("contexts") or {})
    contexts["tracecat_api_request"] = {"method": method, "route": route}
    event["contexts"] = dict(contexts)


def _sanitize_stacktrace(stacktrace: object) -> dict[str, object] | None:
    """Copy only privacy-reviewed stack-frame metadata."""
    if not isinstance(stacktrace, Mapping):
        return None
    values = stacktrace.get("frames")
    if not isinstance(values, list):
        return None

    frames: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        frame: dict[str, object] = {}
        for field in _ALLOWED_FRAME_STRING_FIELDS:
            if isinstance(field_value := value.get(field), str):
                frame[field] = field_value
        for field in _ALLOWED_FRAME_INTEGER_FIELDS:
            if isinstance(field_value := value.get(field), int) and not isinstance(
                field_value, bool
            ):
                frame[field] = field_value
        for field in _ALLOWED_FRAME_BOOLEAN_FIELDS:
            if isinstance(field_value := value.get(field), bool):
                frame[field] = field_value
        frames.append(frame)
    return {"frames": frames}


def _sanitize_mechanism(mechanism: object) -> dict[str, object] | None:
    """Copy only non-payload exception-mechanism metadata."""
    if not isinstance(mechanism, Mapping):
        return None

    sanitized: dict[str, object] = {}
    for field in _ALLOWED_MECHANISM_STRING_FIELDS:
        if isinstance(field_value := mechanism.get(field), str):
            sanitized[field] = field_value
    for field in _ALLOWED_MECHANISM_BOOLEAN_FIELDS:
        if isinstance(field_value := mechanism.get(field), bool):
            sanitized[field] = field_value
    for field in _ALLOWED_MECHANISM_INTEGER_FIELDS:
        if isinstance(field_value := mechanism.get(field), int) and not isinstance(
            field_value, bool
        ):
            sanitized[field] = field_value
    return sanitized or None


def _sanitize_exception(
    exception: object, *, safe_value: str
) -> dict[Literal["values"], list[dict[str, Any]]] | None:
    """Rebuild exception values without copying open-ended payload fields."""
    if not isinstance(exception, Mapping):
        return None
    values = exception.get("values")
    if not isinstance(values, list):
        return None

    # Sentry models exception values as dict[str, Any]. Validate every copied
    # field here before matching that SDK wire type.
    sanitized_values: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        sanitized_value: dict[str, object] = {"value": safe_value}
        for field in _ALLOWED_EXCEPTION_STRING_FIELDS:
            if isinstance(field_value := value.get(field), str):
                sanitized_value[field] = field_value
        if stacktrace := _sanitize_stacktrace(value.get("stacktrace")):
            sanitized_value["stacktrace"] = stacktrace
        if mechanism := _sanitize_mechanism(value.get("mechanism")):
            sanitized_value["mechanism"] = mechanism
        sanitized_values.append(sanitized_value)

    if not sanitized_values:
        return None
    return {"values": sanitized_values}


def _sanitize_event(
    event: Event,
    *,
    safe_value: str,
    allowed_event_fields: frozenset[str],
    allowed_tags: frozenset[str],
    allowed_context_fields: _ContextFieldPolicy,
) -> Event:
    """Reduce a Sentry event to the shared privacy-reviewed schema."""
    sanitized_event = cast(
        Event,
        {key: event[key] for key in allowed_event_fields if key in event},
    )

    tags = event.get("tags")
    sanitized_event["tags"] = (
        {
            key: value
            for key, value in tags.items()
            if key in allowed_tags and isinstance(value, str)
        }
        if isinstance(tags, Mapping)
        else {}
    )
    contexts = event.get("contexts")
    sanitized_contexts: dict[str, dict[str, Any]] = {}
    if isinstance(contexts, Mapping):
        for context_name, allowed_fields in allowed_context_fields.items():
            context = contexts.get(context_name)
            if not isinstance(context, Mapping):
                continue
            sanitized_contexts[context_name] = {
                key: value
                for key, value in context.items()
                if key in allowed_fields and isinstance(value, (bool, float, int, str))
            }
    sanitized_event["contexts"] = sanitized_contexts

    if exception := _sanitize_exception(event.get("exception"), safe_value=safe_value):
        sanitized_event["exception"] = exception

    return sanitized_event


def _sanitize_platform_event(event: Event, hint: Hint) -> Event | None:
    """Drop non-platform events and strip payload-bearing Sentry fields."""
    del hint
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    if tags.get(SentryTag.ERROR_OWNER) != "platform":
        return None
    kind = tags.get(SentryTag.ERROR_KIND, "unclassified")
    return _sanitize_event(
        event,
        safe_value=f"Tracecat platform failure ({kind})",
        allowed_event_fields=_WORKER_ALLOWED_EVENT_FIELDS,
        allowed_tags=_WORKER_ALLOWED_TAGS,
        allowed_context_fields=_WORKER_ALLOWED_CONTEXT_FIELDS,
    )


def _sanitize_api_event(
    event: Event,
    hint: Hint,
    *,
    service_name: str,
) -> Event:
    """Strip API events to stable, privacy-reviewed metadata."""
    del hint
    tags = cast(MutableMapping[str, Any], event.get("tags") or {})
    tags[SentryTag.SERVICE_NAME.value] = service_name
    _enrich_api_request_event(event, tags)
    event["tags"] = dict(tags)
    return _sanitize_event(
        event,
        safe_value="Tracecat API failure",
        allowed_event_fields=_API_ALLOWED_EVENT_FIELDS,
        allowed_tags=_API_ALLOWED_TAGS,
        allowed_context_fields=_API_ALLOWED_CONTEXT_FIELDS,
    )


def _initialize_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    service_name: str,
    integrations: list[Integration],
    before_send: _BeforeSend,
    transport: Transport | None = None,
) -> None:
    """Initialize a Sentry client with an explicit service privacy profile."""
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
        before_send=before_send,
        transport=transport,
    )


def initialize_worker_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    transport: Transport | None = None,
) -> None:
    """Initialize explicit platform-failure capture for a worker process."""
    _initialize_sentry(
        dsn=dsn,
        environment=environment,
        release=release,
        service_name=config.TRACECAT__SERVICE_NAME,
        integrations=[AtexitIntegration()],
        before_send=_sanitize_platform_event,
        transport=transport,
    )


def initialize_api_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    transport: Transport | None = None,
) -> None:
    """Initialize privacy-bounded automatic capture for the API process."""
    service_name = config.TRACECAT__SERVICE_NAME
    _initialize_sentry(
        dsn=dsn,
        environment=environment,
        release=release,
        service_name=service_name,
        integrations=[
            AtexitIntegration(),
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
        ],
        before_send=partial(_sanitize_api_event, service_name=service_name),
        transport=transport,
    )


def _initialize_sentry_from_environment(initializer: _SentryInitializer) -> None:
    """Best-effort initialize one explicit Sentry service profile."""
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
    try:
        initializer(
            dsn=dsn,
            environment=environment,
            release=f"tracecat@{APP_VERSION}",
        )
    except Exception as error:
        logger.warning(
            "Failed to initialize Sentry; continuing without telemetry",
            reporting_error_type=type(error).__name__,
        )
        return
    logger.info(
        "Sentry initialized",
        environment=environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )


def initialize_worker_sentry_from_environment() -> None:
    """Best-effort initialize the worker Sentry profile from the environment."""
    _initialize_sentry_from_environment(initialize_worker_sentry)


def initialize_api_sentry_from_environment() -> None:
    """Best-effort initialize the API Sentry profile from the environment."""
    _initialize_sentry_from_environment(initialize_api_sentry)
