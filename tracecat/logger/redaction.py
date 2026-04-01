from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, SecretBytes, SecretStr

from tracecat import config
from tracecat.context_state import ctx_log_masks
from tracecat.logger.context import inject_context_fields
from tracecat.logger.hashing import (
    LogIdentifierType,
    compute_identifier_hash,
)
from tracecat.secrets.common import apply_masks

MASK_TEXT = "[REDACTED]"
MASK_EMAIL = "[REDACTED_EMAIL]"
MASK_IP = "[REDACTED_IP]"
MASK_URL = "[REDACTED_URL]"
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
    "email": LogIdentifierType.EMAIL,
    "externalaccountid": LogIdentifierType.EXTERNAL_ACCOUNT_ID,
    "username": LogIdentifierType.USERNAME,
}

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


def is_json_logging_enabled() -> bool:
    return config.TRACECAT__APP_ENV in {"staging", "production"}


def is_verbose_payload_logging_enabled() -> bool:
    return _VERBOSE_LOG_PAYLOADS_ENABLED


def maybe_warn_verbose_payload_logging_ignored() -> None:
    if _VERBOSE_LOG_PAYLOADS_IGNORED:
        logging.getLogger(__name__).warning(
            "Ignoring TRACECAT__UNSAFE_ENABLE_VERBOSE_LOG_PAYLOADS outside development"
        )


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.casefold())


def _matches_any_marker(key: str | None, markers: Iterable[str]) -> bool:
    if not key:
        return False
    normalized = key.casefold()
    return any(marker in normalized for marker in markers)


def _identifier_type_for_field(field_name: str) -> LogIdentifierType | None:
    return _IDENTIFIER_FIELD_BY_KEY.get(_normalize_key(field_name))


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
                if hash_value := compute_identifier_hash(identifier_type, item):
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
                hash_value = compute_identifier_hash(identifier_type, scalar)
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
    merged_fields = inject_context_fields(fields)
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
