from __future__ import annotations

import base64
import hashlib
import hmac
from threading import Lock

import boto3
from botocore.exceptions import ClientError

from tracecat import config

_HMAC_SECRET_LOCK = Lock()
_HMAC_SECRET_CACHE: bytes | None = None
TOKENIZED_VISIBILITY_PREFIX = "h1:"


def _coerce_secret(secret: str) -> bytes:
    return secret.encode("utf-8")


def _load_hmac_secret() -> bytes:
    global _HMAC_SECRET_CACHE
    with _HMAC_SECRET_LOCK:
        if _HMAC_SECRET_CACHE is not None:
            return _HMAC_SECRET_CACHE

        if config.TEMPORAL__VISIBILITY_HMAC_KEY:
            _HMAC_SECRET_CACHE = _coerce_secret(config.TEMPORAL__VISIBILITY_HMAC_KEY)
            return _HMAC_SECRET_CACHE

        if config.TEMPORAL__VISIBILITY_HMAC_KEY__ARN:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            try:
                response = client.get_secret_value(
                    SecretId=config.TEMPORAL__VISIBILITY_HMAC_KEY__ARN
                )
            except ClientError as e:
                raise RuntimeError(
                    "Failed to retrieve Temporal visibility HMAC key"
                ) from e

            secret_string = response.get("SecretString")
            if not secret_string and response.get("SecretBinary"):
                secret_string = base64.b64decode(response["SecretBinary"]).decode(
                    "utf-8"
                )
            if not secret_string:
                raise RuntimeError("Temporal visibility HMAC key secret is empty")
            _HMAC_SECRET_CACHE = _coerce_secret(secret_string)
            return _HMAC_SECRET_CACHE

        if config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY:
            _HMAC_SECRET_CACHE = _coerce_secret(config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY)
            return _HMAC_SECRET_CACHE

        raise RuntimeError(
            "Temporal visibility HMAC key is not configured. Set "
            "TEMPORAL__VISIBILITY_HMAC_KEY or TEMPORAL__VISIBILITY_HMAC_KEY__ARN."
        )


def tokenize_visibility_value(value: str) -> str:
    """Tokenize a Temporal visibility value with deterministic HMAC."""
    secret = _load_hmac_secret()
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{TOKENIZED_VISIBILITY_PREFIX}{digest}"


def is_tokenized_visibility_value(value: str | None) -> bool:
    return bool(value and value.startswith(TOKENIZED_VISIBILITY_PREFIX))


def reset_temporal_visibility_secret_cache() -> None:
    global _HMAC_SECRET_CACHE
    with _HMAC_SECRET_LOCK:
        _HMAC_SECRET_CACHE = None
