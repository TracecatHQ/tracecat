"""Configuration management for tracecat-admin CLI."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError

__all__ = [
    "CONFIG_PATH",
    "Config",
    "clear_cookies",
    "get_config",
    "load_cookies",
    "resolve_log_redaction_hmac_key",
    "save_cookies",
]

CONFIG_PATH = Path.home() / ".tracecat_admin.json"


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("true", "1", "yes", "y", "on")


def save_cookies(cookies: httpx.Cookies) -> None:
    """Save cookies to the config file."""
    CONFIG_PATH.write_text(json.dumps({"cookies": dict(cookies)}))
    CONFIG_PATH.chmod(0o600)  # Restrict to owner read/write only


def load_cookies() -> httpx.Cookies:
    """Load cookies from the config file."""
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return httpx.Cookies(data.get("cookies", {}))
    except (FileNotFoundError, json.JSONDecodeError):
        return httpx.Cookies()


def clear_cookies() -> None:
    """Clear saved cookies."""
    CONFIG_PATH.unlink(missing_ok=True)


@dataclass(frozen=True)
class Config:
    """CLI configuration loaded from environment variables."""

    api_url: str
    service_key: str | None
    db_uri: str | None
    ee_multi_tenant: bool
    log_redaction_hmac_key: str | None
    log_redaction_hmac_key_arn: str | None

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables."""
        return cls(
            api_url=os.environ.get("TRACECAT__API_URL", "http://localhost:8000").rstrip(
                "/"
            ),
            service_key=os.environ.get("TRACECAT__SERVICE_KEY"),
            db_uri=os.environ.get("TRACECAT__DB_URI"),
            ee_multi_tenant=_parse_bool(os.environ.get("TRACECAT__EE_MULTI_TENANT")),
            log_redaction_hmac_key=os.environ.get("TRACECAT__LOG_REDACTION_HMAC_KEY")
            or None,
            log_redaction_hmac_key_arn=os.environ.get(
                "TRACECAT__LOG_REDACTION_HMAC_KEY__ARN"
            )
            or None,
        )


def get_config() -> Config:
    """Get the current configuration."""
    return Config.from_env()


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


def resolve_log_redaction_hmac_key(config: Config | None = None) -> str:
    """Resolve the local log redaction HMAC key from env or AWS Secrets Manager."""
    resolved_config = config or get_config()
    if resolved_config.log_redaction_hmac_key:
        return resolved_config.log_redaction_hmac_key

    if not resolved_config.log_redaction_hmac_key_arn:
        raise ValueError(
            "Set TRACECAT__LOG_REDACTION_HMAC_KEY or "
            "TRACECAT__LOG_REDACTION_HMAC_KEY__ARN"
        )

    try:
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager")
        response = client.get_secret_value(
            SecretId=resolved_config.log_redaction_hmac_key_arn
        )
    except (BotoCoreError, ClientError) as exc:
        raise ValueError(
            "Failed to retrieve TRACECAT__LOG_REDACTION_HMAC_KEY__ARN from AWS "
            "Secrets Manager"
        ) from exc

    secret_string = _decode_secret_value(
        secret_string=response.get("SecretString"),
        secret_binary=response.get("SecretBinary"),
    )

    try:
        secret_payload = json.loads(secret_string)
    except json.JSONDecodeError:
        secret_payload = None

    if isinstance(secret_payload, dict):
        for field_name in (
            "key",
            "value",
            "secret",
            "hmac_key",
            "log_redaction_hmac_key",
        ):
            if isinstance(value := secret_payload.get(field_name), str) and value:
                return value
        raise ValueError(
            "Log redaction HMAC secret JSON must include one of: key, value, "
            "secret, hmac_key, log_redaction_hmac_key"
        )

    return secret_string
