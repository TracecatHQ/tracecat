"""Tests for the provider-neutral SMTP transport."""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import AsyncMock

import pytest

from tracecat import config
from tracecat.email import transport as transport_module
from tracecat.email.transport import (
    EmailDeliveryError,
    OutboundEmail,
    SMTPTransport,
)


@pytest.fixture
def anyio_backend() -> str:
    """Local copy so this module runs standalone with --noconftest."""
    return "asyncio"


def _set_smtp_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    from_addr: str | None = None,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", host)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", user)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", password)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", from_addr)


@pytest.mark.parametrize(
    ("host", "user", "password", "from_addr", "expected"),
    [
        (None, None, None, None, False),
        ("smtp.example.com", None, None, None, False),
        ("smtp.example.com", "relay", "secret", None, False),
        (
            "smtp.example.com",
            "relay",
            "secret",
            "Tracecat <no-reply@example.com>",
            True,
        ),
    ],
)
def test_from_config_requires_every_setting(
    monkeypatch: pytest.MonkeyPatch,
    host: str | None,
    user: str | None,
    password: str | None,
    from_addr: str | None,
    expected: bool,
) -> None:
    _set_smtp_config(
        monkeypatch, host=host, user=user, password=password, from_addr=from_addr
    )

    assert (SMTPTransport.from_config() is not None) is expected


def test_from_config_returns_configured_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host="smtp.example.com",
        user="relay",
        password="secret",
        from_addr="Tracecat <no-reply@example.com>",
    )

    transport = SMTPTransport.from_config()

    assert transport is not None
    assert transport.host == "smtp.example.com"
    assert transport.port == 587
    assert transport.username == "relay"
    assert transport.password == "secret"
    assert transport.from_addr == "Tracecat <no-reply@example.com>"


def _outbound(to: str = "invitee@example.com") -> OutboundEmail:
    return OutboundEmail(
        to=(to,),
        subject="Invitation",
        html="<p>Join</p>",
        text="Join",
    )


@pytest.mark.parametrize(
    ("port", "use_tls", "start_tls"),
    [(587, False, True), (465, True, False)],
)
@pytest.mark.anyio
async def test_smtp_transport_send_builds_mime_and_selects_tls(
    monkeypatch: pytest.MonkeyPatch, port: int, use_tls: bool, start_tls: bool
) -> None:
    send = AsyncMock()
    monkeypatch.setattr(transport_module.aiosmtplib, "send", send)
    transport = SMTPTransport(
        host="smtp.example.com",
        port=port,
        username="relay",
        password="secret",
        from_addr="Tracecat <no-reply@example.com>",
    )

    await transport.send(_outbound())

    send.assert_awaited_once()
    await_args = send.await_args
    assert await_args is not None
    mime = await_args.args[0]
    assert isinstance(mime, EmailMessage)
    assert await_args.kwargs == {
        "hostname": "smtp.example.com",
        "port": port,
        "username": "relay",
        "password": "secret",
        "use_tls": use_tls,
        "start_tls": start_tls,
        "timeout": 20,
    }
    assert mime["From"] == "Tracecat <no-reply@example.com>"
    assert mime["To"] == "invitee@example.com"
    assert mime["Subject"] == "Invitation"


@pytest.mark.anyio
async def test_smtp_transport_send_hides_host_and_recipient_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transport_module.aiosmtplib,
        "send",
        AsyncMock(side_effect=RuntimeError("550 invitee@example.com rejected")),
    )
    transport = SMTPTransport(
        host="smtp.customer.internal",
        port=587,
        username="relay",
        password="secret",
        from_addr="Tracecat <no-reply@example.com>",
    )

    with pytest.raises(EmailDeliveryError) as exc_info:
        await transport.send(_outbound())

    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "587" in message
    assert "smtp.customer.internal" not in message
    assert "invitee@example.com" not in message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__
