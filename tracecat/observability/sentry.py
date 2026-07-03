"""Shared Sentry initialization and capture helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.types import Event, Hint

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.logger import logger

REDACTED_VALUE = "[Filtered]"
_MAX_SCRUB_DEPTH = 8
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_URL_PATH_SECRET_RES = (
    re.compile(r"^(.*?/webhooks/[^/]+/)([^/]+)(?=/|$)"),
    re.compile(r"^(.*?/invitations/token/)([^/]+)(?=/|$)"),
    re.compile(r"^(.*?/agent/channels/[^/]+/)([^/]+)(?=/|$)"),
)
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "dsn",
        "jwt",
        "keyring",
        "password",
        "private_key",
        "secret",
        "set-cookie",
        "signature",
        "token",
    }
)
_SENSITIVE_QUERY_KEY_PARTS = _SENSITIVE_KEY_PARTS | frozenset({"code", "state"})
_REQUEST_URL_KEYS = frozenset({"url", "request_url"})
_REQUEST_QUERY_KEYS = frozenset({"query_string", "querystring"})


def init_sentry(
    service_name: str,
    *,
    integrations: Sequence[Integration] | None = None,
) -> bool:
    """Initialize Sentry for a Tracecat service.

    Args:
        service_name: Logical service name, such as ``api`` or ``executor``.
        integrations: Optional Sentry integrations for the current runtime.

    Returns:
        True when Sentry is configured for the current process.
    """
    sentry_dsn = os.environ.get("SENTRY_DSN")
    if not sentry_dsn:
        return False

    app_env = config.TRACECAT__APP_ENV
    temporal_namespace = config.TEMPORAL__CLUSTER_NAMESPACE
    sentry_environment = (
        config.SENTRY_ENVIRONMENT_OVERRIDE or f"{app_env}-{temporal_namespace}"
    )

    if not sentry_sdk.is_initialized():
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=sentry_environment,
            release=f"tracecat@{APP_VERSION}",
            integrations=list(integrations or ()),
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            before_send=_before_send,
        )

    sentry_sdk.set_tag("tracecat.service", service_name)
    logger.info(
        "Sentry initialized",
        service=service_name,
        environment=sentry_environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )
    return True


def capture_exception(
    exc: BaseException,
    *,
    tags: Mapping[str, Any] | None = None,
    contexts: Mapping[str, Any] | None = None,
) -> str | None:
    """Capture an exception with optional scrubbed tags and contexts."""
    if not sentry_sdk.is_initialized():
        return None

    with sentry_sdk.new_scope() as scope:
        for key, value in (tags or {}).items():
            scope.set_tag(key, str(value))
        for key, value in (contexts or {}).items():
            if value is None:
                continue
            scrubbed = _scrub(value)
            if isinstance(scrubbed, Mapping):
                scope.set_context(key, cast(dict[str, Any], scrubbed))
            else:
                scope.set_context(key, {"value": scrubbed})
        return sentry_sdk.capture_exception(exc)


def _before_send(event: Event, hint: Hint) -> Event | None:
    del hint
    return cast(Event, _scrub(event))


def _scrub(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_SCRUB_DEPTH:
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        scrubbed: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                scrubbed[key] = REDACTED_VALUE
            else:
                scrubbed[key] = _scrub_request_value(
                    key,
                    _scrub(raw_value, depth=depth + 1),
                )
        return scrubbed
    if isinstance(value, tuple):
        return tuple(_scrub(item, depth=depth + 1) for item in value)
    if isinstance(value, list):
        return [_scrub(item, depth=depth + 1) for item in value]
    if isinstance(value, set):
        return sorted(str(_scrub(item, depth=depth + 1)) for item in value)
    return value


def _scrub_request_value(key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value

    normalized_key = _normalize_sensitive_key(key)
    if normalized_key in _REQUEST_QUERY_KEYS:
        return _scrub_query_string(value)
    if normalized_key in _REQUEST_URL_KEYS:
        return _scrub_url_query(value)
    return value


def _scrub_url_query(value: str) -> str:
    try:
        parsed_url = urlsplit(value)
    except ValueError:
        return value
    scrubbed_query = _scrub_query_string(parsed_url.query) if parsed_url.query else ""
    return urlunsplit(
        parsed_url._replace(
            netloc=_strip_url_userinfo(parsed_url.netloc),
            path=redact_url_path_secrets(parsed_url.path),
            query=scrubbed_query,
            fragment="",
        )
    )


def redact_url_path_secrets(path: str) -> str:
    """Redact sensitive path segments that can appear in request URLs."""
    for pattern in _URL_PATH_SECRET_RES:
        path = pattern.sub(rf"\1{REDACTED_VALUE}", path)
    return path


def _strip_url_userinfo(netloc: str) -> str:
    return netloc.rsplit("@", maxsplit=1)[-1]


def _scrub_query_string(value: str) -> str:
    if not value:
        return value

    params = parse_qsl(value.replace(";", "&"), keep_blank_values=True)
    if not params:
        return value

    return urlencode(
        [
            (key, REDACTED_VALUE if _is_sensitive_query_key(key) else val)
            for key, val in params
        ],
        doseq=True,
    )


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_sensitive_key(key)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = _normalize_sensitive_key(key)
    return any(part in normalized for part in _SENSITIVE_QUERY_KEY_PARTS)


def _normalize_sensitive_key(key: str) -> str:
    camel_split = _CAMEL_CASE_BOUNDARY_RE.sub("_", key)
    return _NON_ALNUM_RE.sub("_", camel_split).casefold().strip("_")
