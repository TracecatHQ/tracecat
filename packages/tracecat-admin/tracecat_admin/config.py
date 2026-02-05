"""Configuration management for tracecat-admin CLI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

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
        )


def get_config() -> Config:
    """Get the current configuration."""
    return Config.from_env()
