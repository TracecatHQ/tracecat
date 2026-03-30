from __future__ import annotations

import base64
import hashlib
import hmac
import json
from enum import StrEnum
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger as base_logger

from tracecat import config

LOG_HASH_VERSION = "v1"
_HMAC_SECRET_JSON_FIELDS = (
    "key",
    "value",
    "secret",
    "hmac_key",
    "log_redaction_hmac_key",
)


class LogIdentifierType(StrEnum):
    EMAIL = "email"
    USERNAME = "username"
    EXTERNAL_ACCOUNT_ID = "external_account_id"


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


def normalize_identifier_value(
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


def compute_identifier_hash(
    identifier_type: LogIdentifierType, value: str
) -> str | None:
    if not (key := resolve_log_redaction_hmac_key()):
        return None
    if not (normalized := normalize_identifier_value(identifier_type, value)):
        return None

    payload = f"{identifier_type.value}:{normalized}".encode()
    digest = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{LOG_HASH_VERSION}_{digest}"
