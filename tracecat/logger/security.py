from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum, StrEnum
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger as base_logger
from pydantic import BaseModel, SecretBytes, SecretStr
from sentry_sdk.scrubber import EventScrubber

from tracecat import config
from tracecat.auth.types import Role
from tracecat.context_state import (
    ctx_log_masks,
    ctx_request_id,
    ctx_role,
    ctx_run,
    ctx_session_id,
)
from tracecat.secrets.common import apply_masks

_bootstrap_logger = logging.getLogger(__name__)

MASK_TEXT = "[REDACTED]"
MASK_EMAIL = "[REDACTED_EMAIL]"
MASK_IP = "[REDACTED_IP]"
MASK_URL = "[REDACTED_URL]"
LOG_HASH_VERSION = "v1"
_SUMMARY_KEY_LIMIT = 32
_SEQUENCE_ITEM_LIMIT = 32
_MESSAGE_MAX_LENGTH = 4096

_VERBOSE_LOG_PAYLOADS_ENABLED = config.TRACECAT__APP_ENV == "development" and (
    config.TRACECAT__UNSAFE_ENABLE_VERBOSE_LOG_PAYLOADS
)
_VERBOSE_LOG_PAYLOADS_IGNORED = config.TRACECAT__APP_ENV != "development" and (
    config.TRACECAT__UNSAFE_ENABLE_VERBOSE_LOG_PAYLOADS
)

_STRUCTURAL_SUMMARY_FIELDS = frozenset(
    {
        "args",
        "body",
        "headers",
        "input",
        "output",
        "params",
        "payload",
        "result",
        "results",
        "task_result",
    }
)
_TRACEBACK_FIELDS = frozenset({"traceback", "stacktrace", "stack_trace"})
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "passwd",
    "password",
    "private_key",
    "secret",
    "set_cookie",
    "token",
)
_URL_KEY_MARKERS = ("callback_url", "redirect_url", "uri", "url")
_IDENTIFIER_FIELD_BY_KEY = {
    "email": "email",
    "externalaccountid": "external_account_id",
    "username": "username",
}
_HMAC_SECRET_JSON_FIELDS = (
    "key",
    "value",
    "secret",
    "hmac_key",
    "log_redaction_hmac_key",
)

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])",
    flags=re.IGNORECASE,
)
_IPV4_PATTERN = re.compile(r"(?<![\w:])((?:\d{1,3}\.){3}\d{1,3})(?![\w:])")
_AUTH_HEADER_PATTERN = re.compile(r"(?i)(authorization:\s*(?:basic|bearer)\s+)[^\s,;]+")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b("
    r"access[_-]?token|api[_-]?key|authorization|cookie|password|passwd|refresh[_-]?token|secret|session[_-]?id|token"
    r")=([^&\s]+)"
)
_URL_USERINFO_PATTERN = re.compile(r"(?i)(https?://[^/\s:@]+:)([^@\s/]+)@")
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")


class LogIdentifierType(StrEnum):
    EMAIL = "email"
    USERNAME = "username"
    EXTERNAL_ACCOUNT_ID = "external_account_id"


def is_json_logging_enabled() -> bool:
    return config.TRACECAT__APP_ENV in {"staging", "production"}


def is_verbose_payload_logging_enabled() -> bool:
    return _VERBOSE_LOG_PAYLOADS_ENABLED


def maybe_warn_verbose_payload_logging_ignored() -> None:
    if _VERBOSE_LOG_PAYLOADS_IGNORED:
        _bootstrap_logger.warning(
            "Ignoring TRACECAT__UNSAFE_ENABLE_VERBOSE_LOG_PAYLOADS outside development"
        )


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.casefold())


def _matches_any_marker(key: str | None, markers: Iterable[str]) -> bool:
    if not key:
        return False
    normalized = key.casefold()
    return any(marker in normalized for marker in markers)


def _mask_value_candidates(masks: Iterable[str] | None = None) -> tuple[str, ...]:
    if masks is not None:
        return tuple(mask for mask in masks if mask)
    context_masks = ctx_log_masks.get() or ()
    return tuple(mask for mask in context_masks if mask)


def _truncate_text(text: str, *, limit: int = _MESSAGE_MAX_LENGTH) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _sanitize_url_string(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return MASK_URL
    return f"{parts.scheme}://{parts.netloc}"


def sanitize_text(
    text: str | None,
    *,
    masks: Iterable[str] | None = None,
    max_length: int = _MESSAGE_MAX_LENGTH,
) -> str | None:
    if text is None:
        return None

    sanitized = apply_masks(text, _mask_value_candidates(masks))
    sanitized = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _URL_USERINFO_PATTERN.sub(r"\1[REDACTED]@", sanitized)
    sanitized = _HTTP_URL_PATTERN.sub(
        lambda match: _sanitize_url_string(match.group(0)),
        sanitized,
    )
    sanitized = _EMAIL_PATTERN.sub(MASK_EMAIL, sanitized)
    sanitized = _IPV4_PATTERN.sub(MASK_IP, sanitized)
    return _truncate_text(sanitized, limit=max_length)


def sanitize_error_text(text: str | None) -> str | None:
    return sanitize_text(text, max_length=2048)


def _decode_secret_value(
    secret_string: str | None, secret_binary: str | bytes | None
) -> str:
    if secret_string:
        return secret_string
    if not secret_binary:
        raise ValueError("Log redaction HMAC secret is empty")

    decoded = base64.b64decode(secret_binary)
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Log redaction HMAC secret must be UTF-8 text when using SecretBinary"
        ) from exc


@lru_cache(maxsize=1)
def resolve_log_redaction_hmac_key() -> str | None:
    if config.TRACECAT__LOG_REDACTION_HMAC_KEY:
        return config.TRACECAT__LOG_REDACTION_HMAC_KEY

    if not config.TRACECAT__LOG_REDACTION_HMAC_KEY__ARN:
        return None

    try:
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager")
        response = client.get_secret_value(
            SecretId=config.TRACECAT__LOG_REDACTION_HMAC_KEY__ARN
        )
    except (BotoCoreError, ClientError) as exc:
        base_logger.warning(
            "Failed to retrieve log redaction HMAC key from AWS Secrets Manager",
            error=str(exc),
        )
        return None

    secret_string = _decode_secret_value(
        secret_string=response.get("SecretString"),
        secret_binary=response.get("SecretBinary"),
    )

    try:
        secret_payload = json.loads(secret_string)
    except json.JSONDecodeError:
        return secret_string

    if not isinstance(secret_payload, dict):
        return secret_string

    for field_name in _HMAC_SECRET_JSON_FIELDS:
        if isinstance(value := secret_payload.get(field_name), str) and value:
            return value
    return None


def _normalize_identifier_value(
    identifier_type: LogIdentifierType, value: str
) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None

    match identifier_type:
        case LogIdentifierType.EMAIL:
            return normalized.casefold()
        case LogIdentifierType.USERNAME | LogIdentifierType.EXTERNAL_ACCOUNT_ID:
            return normalized


def _compute_identifier_hash(
    identifier_type: LogIdentifierType, value: str
) -> str | None:
    if not (key := resolve_log_redaction_hmac_key()):
        return None
    if not (normalized := _normalize_identifier_value(identifier_type, value)):
        return None

    payload = f"{identifier_type.value}:{normalized}".encode()
    digest = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{LOG_HASH_VERSION}_{digest}"


def _identifier_type_for_field(field_name: str) -> LogIdentifierType | None:
    match _IDENTIFIER_FIELD_BY_KEY.get(_normalize_key(field_name)):
        case "email":
            return LogIdentifierType.EMAIL
        case "username":
            return LogIdentifierType.USERNAME
        case "external_account_id":
            return LogIdentifierType.EXTERNAL_ACCOUNT_ID
        case _:
            return None


def _json_safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return None


def _value_type_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "array"
    return type(value).__name__


def summarize_log_value(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        keys = [str(key) for key in list(value.keys())[:_SUMMARY_KEY_LIMIT]]
        return {
            "type": "object",
            "item_count": len(value),
            "keys": keys,
            "truncated": len(value) > _SUMMARY_KEY_LIMIT,
        }

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return {
            "type": "array",
            "item_count": len(value),
            "truncated": len(value) > _SEQUENCE_ITEM_LIMIT,
        }

    return {"type": _value_type_name(value)}


def _coerce_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()}


def _coerce_role(value: Any) -> Role | None:
    if isinstance(value, Role):
        return value
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        payload = value
    else:
        return None

    try:
        return Role.model_validate(payload)
    except Exception:
        return None


def _inject_role_fields(record: dict[str, Any], role: Role | None) -> None:
    if role is None:
        return
    if role.organization_id is not None:
        record.setdefault("organization_id", str(role.organization_id))
    if role.workspace_id is not None:
        record.setdefault("workspace_id", str(role.workspace_id))
    if role.user_id is not None:
        record.setdefault("user_id", str(role.user_id))
    record.setdefault("role_type", role.type)
    record.setdefault("role_service_id", role.service_id)


def _inject_context_fields(record: dict[str, Any]) -> dict[str, Any]:
    injected = dict(record)

    if (role := _coerce_role(injected.pop("role", None))) is None:
        role = ctx_role.get()
    _inject_role_fields(injected, role)

    if (run_context := ctx_run.get()) is not None:
        injected.setdefault("wf_id", str(run_context.wf_id))
        injected.setdefault("wf_exec_id", str(run_context.wf_exec_id))
        injected.setdefault("wf_run_id", str(run_context.wf_run_id))

    if (request_id := ctx_request_id.get()) is not None:
        injected.setdefault("request_id", request_id)
    if (session_id := ctx_session_id.get()) is not None:
        injected.setdefault("session_id", str(session_id))
    return injected


def _sanitize_mapping(
    value: Mapping[Any, Any],
    *,
    field_name: str | None = None,
    preserve_structure: bool,
) -> dict[str, Any]:
    mapping = _coerce_mapping(value)
    if (
        field_name in _STRUCTURAL_SUMMARY_FIELDS
        and not preserve_structure
        and not is_verbose_payload_logging_enabled()
    ):
        return summarize_log_value(mapping)

    sanitized: dict[str, Any] = {}
    for key, item in mapping.items():
        sanitized_key = str(key)
        if _matches_any_marker(sanitized_key, _SENSITIVE_KEY_MARKERS):
            sanitized[sanitized_key] = MASK_TEXT
            continue
        if identifier_type := _identifier_type_for_field(sanitized_key):
            if isinstance(item, str):
                sanitized[sanitized_key] = MASK_TEXT
                if preserve_structure:
                    continue
                if hash_value := _compute_identifier_hash(identifier_type, item):
                    sanitized[f"{sanitized_key}_hash"] = hash_value
                continue
        sanitized[sanitized_key] = sanitize_log_value(
            item,
            field_name=sanitized_key,
            preserve_structure=preserve_structure,
        )
    return sanitized


def sanitize_log_value(
    value: Any,
    *,
    field_name: str | None = None,
    preserve_structure: bool = False,
) -> Any:
    if (scalar := _json_safe_scalar(value)) is not None:
        if isinstance(scalar, str):
            if _matches_any_marker(field_name, _URL_KEY_MARKERS):
                return _sanitize_url_string(scalar)
            if _matches_any_marker(field_name, _SENSITIVE_KEY_MARKERS):
                return MASK_TEXT
            if identifier_type := _identifier_type_for_field(field_name or ""):
                if preserve_structure:
                    return MASK_TEXT
                hash_value = _compute_identifier_hash(identifier_type, scalar)
                return {
                    "value": MASK_TEXT,
                    "hash": hash_value,
                }
            return sanitize_text(scalar)
        return scalar

    if isinstance(value, SecretStr | SecretBytes):
        return MASK_TEXT
    if isinstance(value, Exception):
        return sanitize_text(str(value))
    if isinstance(value, bytes | bytearray):
        return f"<bytes {len(value)}>"
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sanitize_log_value(
            dataclasses.asdict(value),
            field_name=field_name,
            preserve_structure=preserve_structure,
        )
    if isinstance(value, BaseModel):
        return sanitize_log_value(
            value.model_dump(mode="json"),
            field_name=field_name,
            preserve_structure=preserve_structure,
        )
    if isinstance(value, Mapping):
        return _sanitize_mapping(
            value,
            field_name=field_name,
            preserve_structure=preserve_structure,
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if (
            field_name in _STRUCTURAL_SUMMARY_FIELDS
            and not preserve_structure
            and not is_verbose_payload_logging_enabled()
        ):
            return summarize_log_value(value)
        items = list(value)
        sanitized_items = [
            sanitize_log_value(item, preserve_structure=preserve_structure)
            for item in items[:_SEQUENCE_ITEM_LIMIT]
        ]
        if len(items) > _SEQUENCE_ITEM_LIMIT:
            sanitized_items.append("...[truncated]")
        return sanitized_items

    return sanitize_text(str(value))


def sanitize_log_fields(
    fields: Mapping[str, Any],
    *,
    preserve_structure: bool = False,
) -> dict[str, Any]:
    merged_fields = _inject_context_fields(dict(fields))
    sanitized: dict[str, Any] = {}
    for key, value in merged_fields.items():
        if key == "client_ip":
            continue
        if key in _TRACEBACK_FIELDS:
            sanitized[key] = sanitize_text(str(value))
            continue
        sanitized[key] = sanitize_log_value(
            value,
            field_name=key,
            preserve_structure=preserve_structure,
        )
        if isinstance(sanitized[key], dict) and set(sanitized[key]) == {
            "value",
            "hash",
        }:
            hash_value = sanitized[key]["hash"]
            sanitized[key] = sanitized[key]["value"]
            if hash_value:
                sanitized[f"{key}_hash"] = hash_value
    return sanitized


def sanitize_log_record(record: Any) -> None:
    record["message"] = sanitize_text(record["message"]) or ""
    record["extra"] = sanitize_log_fields(record["extra"])


def build_log_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    extra = dict(record["extra"])
    payload = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": sanitize_text(str(record["message"])) or "",
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
        "process": record["process"].id,
    }
    return {**payload, **extra}


def sanitize_workflow_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    return sanitize_log_fields(fields)


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
