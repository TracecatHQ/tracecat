"""Privacy-bounded Sentry configuration for explicit platform error capture."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from tracecat.runtime.errors import RuntimeErrorClassification


@dataclass(frozen=True, slots=True)
class WorkflowFailureEventContext:
    """Privacy-reviewed workflow metadata attached to a platform event."""

    run_id: str
    workflow_type: str
    attempt: int
    trigger_type: str


class SentryTag(StrEnum):
    """Stable, privacy-reviewed tag keys for workflow failure events."""

    ERROR_OWNER = "tracecat.error.owner"
    ERROR_KIND = "tracecat.error.kind"
    ERROR_RETRY_DISPOSITION = "tracecat.error.retry_disposition"
    ERROR_CAUSE_TYPE = "tracecat.error.cause_type"
    WORKFLOW_TYPE = "temporal.workflow.type"
    WORKFLOW_ATTEMPT = "temporal.workflow.attempt"
    TRIGGER_TYPE = "tracecat.trigger_type"


_ALLOWED_TAGS = frozenset(SentryTag)
_ALLOWED_CONTEXTS = frozenset({"runtime", "tracecat_workflow"})


def capture_platform_failure(
    error: BaseException,
    classification: RuntimeErrorClassification,
    context: WorkflowFailureEventContext,
) -> None:
    """Capture one classified platform event without workflow payload data."""
    client = sentry_sdk.get_client()
    if not client.is_active() or client.options.get("dsn") is None:
        return

    with sentry_sdk.isolation_scope() as scope:
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
    event["contexts"] = {
        key: value for key, value in contexts.items() if key in _ALLOWED_CONTEXTS
    }
    for field in ("breadcrumbs", "extra", "request", "user"):
        event.pop(field, None)

    safe_value = (
        "Tracecat platform runtime failure "
        f"({tags.get(SentryTag.ERROR_KIND, 'unclassified')})"
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
