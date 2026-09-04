"""Tests for provider-neutral invitation email delivery."""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks

from tracecat import config
from tracecat.email import client
from tracecat.email.client import (
    EmailDeliveryError,
    Mailer,
    OutboundEmail,
    SMTPTransport,
    build_accept_url,
    invitation_email,
    is_email_configured,
)
from tracecat.email.templates import render_invitation_email


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
    domain: str | None = None,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", host)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", user)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", password)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_DOMAIN", domain)


@pytest.mark.parametrize(
    ("host", "user", "password", "domain", "expected"),
    [
        (None, None, None, None, False),
        ("smtp.example.com", None, None, None, False),
        ("smtp.example.com", "relay", "secret", None, False),
        ("smtp.example.com", "relay", "secret", "example.com", True),
    ],
)
def test_email_configuration_requires_every_setting(
    monkeypatch: pytest.MonkeyPatch,
    host: str | None,
    user: str | None,
    password: str | None,
    domain: str | None,
    expected: bool,
) -> None:
    _set_smtp_config(
        monkeypatch, host=host, user=user, password=password, domain=domain
    )

    assert is_email_configured() is expected


def test_invitation_template_escapes_html_and_subject_controls() -> None:
    subject, html, text = render_invitation_email(
        accept_url='https://app.example.com/invitations/accept?token=a&b="x',
        context_name="Acme\r\nBcc: attacker@example.com<script>",
    )

    assert "\r" not in subject
    assert "\n" not in subject
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "token=a&amp;b=&quot;x" in html
    assert 'token=a&b="x' in text


def test_build_accept_url_handles_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com/")

    assert build_accept_url("token-123") == (
        "https://app.example.com/invitations/accept?token=token-123"
    )


def test_invitation_logo_url_handles_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com/")

    _, html, _ = render_invitation_email(
        accept_url="https://app.example.com/invitations/accept?token=t",
        context_name="Acme",
    )

    assert 'src="https://app.example.com/icon.png"' in html


def _outbound(to: str = "invitee@example.com") -> OutboundEmail:
    return OutboundEmail(
        to=(to,),
        subject="Invitation",
        html="<p>Join</p>",
        text="Join",
        from_addr="Tracecat <no-reply@example.com>",
        headers={"In-Reply-To": "<message@example.com>"},
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
    monkeypatch.setattr(client.aiosmtplib, "send", send)
    transport = SMTPTransport(
        host="smtp.example.com", port=port, username="relay", password="secret"
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
    }
    assert mime["From"] == "Tracecat <no-reply@example.com>"
    assert mime["To"] == "invitee@example.com"
    assert mime["Subject"] == "Invitation"
    assert mime["In-Reply-To"] == "<message@example.com>"


def test_invitation_email_uses_platform_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EMAIL_DOMAIN", "mail.example.com")
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")

    message = invitation_email(
        to="invitee@example.com", organization_name="Acme", token="token-123"
    )

    assert message.to == ("invitee@example.com",)
    assert message.subject == "Join Acme on Tracecat"
    assert message.from_addr == "Tracecat <no-reply@mail.example.com>"
    assert "token-123" in message.text


@pytest.mark.anyio
async def test_mailer_queues_send_as_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host="smtp.example.com",
        user="relay",
        password="secret",
        domain="example.com",
    )
    send = AsyncMock()
    monkeypatch.setattr(SMTPTransport, "send", send)
    tasks = BackgroundTasks()

    Mailer(tasks).deliver(_outbound())

    assert len(tasks.tasks) == 1
    await tasks()
    send.assert_awaited_once()


@pytest.mark.anyio
async def test_mailer_task_logs_then_raises_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host="smtp.example.com",
        user="relay",
        password="secret",
        domain="example.com",
    )
    monkeypatch.setattr(
        SMTPTransport, "send", AsyncMock(side_effect=RuntimeError("relay down"))
    )
    log_error = Mock()
    monkeypatch.setattr(client.logger, "error", log_error)
    tasks = BackgroundTasks()

    Mailer(tasks).deliver(_outbound())
    with pytest.raises(EmailDeliveryError) as exc_info:
        await tasks()

    log_error.assert_called_once()
    assert log_error.call_args.kwargs["error_type"] == "RuntimeError"
    assert "invitee@example.com" not in repr(log_error.call_args)
    assert "RuntimeError" in str(exc_info.value)
    assert "invitee@example.com" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_outbound_email_copies_headers() -> None:
    headers = {"X-Invite-Id": "first"}
    message = OutboundEmail(
        to=("invitee@example.com",),
        subject="Invitation",
        html="<p>Join</p>",
        text="Join",
        from_addr="Tracecat <no-reply@example.com>",
        headers=headers,
    )
    headers["X-Invite-Id"] = "second"

    assert message.headers["X-Invite-Id"] == "first"


def test_mailer_is_a_noop_when_email_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(monkeypatch)
    tasks = BackgroundTasks()

    Mailer(tasks).deliver(_outbound())

    assert tasks.tasks == []
