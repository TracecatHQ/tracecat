"""Configuration management for tracecat-admin CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """CLI configuration loaded from environment variables."""

    api_url: str
    service_key: str | None
    db_uri: str | None

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables."""
        return cls(
            api_url=os.environ.get("TRACECAT__API_URL", "http://localhost:8000").rstrip(
                "/"
            ),
            service_key=os.environ.get("TRACECAT__SERVICE_KEY"),
            db_uri=os.environ.get("TRACECAT__DB_URI"),
        )


def get_config() -> Config:
    """Get the current configuration."""
    return Config.from_env()
