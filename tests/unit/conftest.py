from __future__ import annotations

import pytest

from tracecat import config


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", "relay")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        config, "TRACECAT__EMAIL_FROM", "Tracecat <no-reply@example.com>"
    )


@pytest.fixture
def smtp_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", None)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", None)
