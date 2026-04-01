from __future__ import annotations

from typing import Any

from sentry_sdk.scrubber import EventScrubber

from tracecat import config
from tracecat.logger.redaction import sanitize_log_value


def configure_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    sentry_sdk_module: Any,
) -> None:
    sentry_sdk_module.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        include_local_variables=config.TRACECAT__APP_ENV == "development",
        event_scrubber=EventScrubber(recursive=True, send_default_pii=False),
        before_send=before_send_sentry_event,
    )


def _sanitize_sentry_value(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_sentry_value(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_sentry_value(item, field_name=field_name) for item in value]
    return sanitize_log_value(value, field_name=field_name, preserve_structure=True)


def before_send_sentry_event(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any]:
    del hint
    sanitized = _sanitize_sentry_value(event)
    if not isinstance(sanitized, dict):
        return event
    return sanitized
