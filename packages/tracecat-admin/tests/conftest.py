"""Test configuration for tracecat-admin CLI tests."""

from __future__ import annotations

import pytest

API_URL = "http://localhost:8000"
SERVICE_KEY = "test-service-key"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure clean environment for tests."""
    for var in ["TRACECAT__SERVICE_KEY", "TRACECAT__DB_URI"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up mock environment variables for API calls."""
    monkeypatch.setenv("TRACECAT__API_URL", API_URL)
    monkeypatch.setenv("TRACECAT__SERVICE_KEY", SERVICE_KEY)
