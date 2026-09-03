"""Tests for provider-neutral invitation email delivery."""

from __future__ import annotations

from email.message import EmailMessage
from typing import ClassVar
from unittest.mock import Mock

import pytest

from tracecat import config
from tracecat.email import client
from tracecat.email.client import (
    InvitationEmail,
    OutboundEmail,
    SMTPTransport,
    build_accept_url,
    is_email_configured,
    send_emails,
    send_invitation_emails,
)
from tracecat.email.templates import render_invitation_email


def _set_smtp_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    email_from: str | None = None,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", host)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", user)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", password)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", email_from)


@pytest.mark.parametrize(
    ("host", "user", "password", "email_from", "expected"),
    [
        (None, None, None, None, False),
        ("smtp.example.com", None, None, None, False),
        ("smtp.example.com", "relay", "secret", "invalid", False),
        ("smtp.example.com", "relay", "secret", "sender@example.com", True),
        (
            "smtp.example.com",
            "relay",
            "secret",
            "Tracecat <sender@example.com>",
            True,
        ),
    ],
)
def test_email_configuration_requires_complete_valid_settings(
    monkeypatch: pytest.MonkeyPatch,
    host: str | None,
    user: str | None,
    password: str | None,
    email_from: str | None,
    expected: bool,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host=host,
        user=user,
        password=password,
        email_from=email_from,
    )

    assert is_email_configured() is expected


def test_invitation_template_escapes_html_and_subject_controls() -> None:
    subject, html, text = render_invitation_email(
        accept_url='https://app.example.com/invitations/accept?token=a&b="x',
        context_name="Acme\r\nBcc: attacker@example.com<script>",
        kind="organization",
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


class _FakeSMTP:
    instances: ClassVar[list[_FakeSMTP]] = []
    fail_login: ClassVar[bool] = False

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        use_tls: bool,
        start_tls: bool,
    ) -> None:
        self.kwargs = {
            "hostname": hostname,
            "port": port,
            "use_tls": use_tls,
            "start_tls": start_tls,
        }
        self.connected = False
        self.logged_in: tuple[str, str] | None = None
        self.close_called = False
        self.quit_called = False
        self.attempted_recipients: list[tuple[str, ...]] = []
        self.sent_messages: list[EmailMessage] = []
        _FakeSMTP.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def login(self, username: str, password: str) -> None:
        if self.fail_login:
            raise RuntimeError("authentication failed")
        self.logged_in = (username, password)

    def close(self) -> None:
        self.close_called = True

    async def quit(self) -> None:
        self.quit_called = True

    async def send_message(
        self,
        message: EmailMessage,
        *,
        sender: str,
        recipients: list[str],
    ) -> None:
        del sender
        attempted = tuple(recipients)
        self.attempted_recipients.append(attempted)
        if "fail@example.com" in attempted:
            raise RuntimeError("rejected")
        self.sent_messages.append(message)


@pytest.fixture(autouse=True)
def _fake_smtp(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSMTP.instances.clear()
    _FakeSMTP.fail_login = False
    monkeypatch.setattr(client.aiosmtplib, "SMTP", _FakeSMTP)


def _outbound(to: str) -> OutboundEmail:
    return OutboundEmail(
        to=(to,),
        subject="Invitation",
        html="<p>Join</p>",
        text="Join",
        from_addr="Tracecat <sender@example.com>",
        headers={"In-Reply-To": "<message@example.com>"},
    )


@pytest.mark.anyio
async def test_smtp_transport_reuses_connection_and_continues_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_error = Mock()
    monkeypatch.setattr(client.logger, "error", log_error)
    transport = SMTPTransport(
        host="smtp.example.com",
        port=587,
        username="relay",
        password="secret",
    )

    async with transport:
        await send_emails(
            [
                _outbound("first@example.com"),
                _outbound("fail@example.com"),
                _outbound("last@example.com"),
            ],
            transport,
        )

    assert len(_FakeSMTP.instances) == 1
    smtp = _FakeSMTP.instances[0]
    assert smtp.kwargs == {
        "hostname": "smtp.example.com",
        "port": 587,
        "use_tls": False,
        "start_tls": True,
    }
    assert smtp.connected is True
    assert smtp.logged_in == ("relay", "secret")
    assert smtp.attempted_recipients == [
        ("first@example.com",),
        ("fail@example.com",),
        ("last@example.com",),
    ]
    assert smtp.quit_called is True
    assert "fail@example.com" not in repr(log_error.call_args_list)


@pytest.mark.anyio
async def test_invitation_flow_renders_and_sends_over_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host="smtp.resend.com",
        user="resend",
        password="secret",
        email_from="Tracecat <sender@example.com>",
    )
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")

    await send_invitation_emails(
        [
            InvitationEmail(
                to="invitee@example.com",
                accept_url=build_accept_url("token-123"),
                context_name="Acme",
                kind="organization",
            )
        ]
    )

    smtp = _FakeSMTP.instances[0]
    assert len(smtp.sent_messages) == 1
    message = smtp.sent_messages[0]
    assert message["From"] == "Tracecat <sender@example.com>"
    assert message["To"] == "invitee@example.com"
    assert message["Subject"] == "Join Acme on Tracecat"
    assert "token-123" in str(message)


@pytest.mark.anyio
async def test_invitation_flow_is_noop_when_email_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(monkeypatch)

    await send_invitation_emails(
        [
            InvitationEmail(
                to="invitee@example.com",
                accept_url="https://app.example.com/invitations/accept?token=token",
                context_name="Acme",
                kind="organization",
            )
        ]
    )

    assert _FakeSMTP.instances == []


@pytest.mark.anyio
async def test_invitation_flow_closes_failed_smtp_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_smtp_config(
        monkeypatch,
        host="smtp.example.com",
        user="relay",
        password="secret",
        email_from="sender@example.com",
    )
    _FakeSMTP.fail_login = True

    await send_invitation_emails(
        [
            InvitationEmail(
                to="invitee@example.com",
                accept_url="https://app.example.com/invitations/accept?token=token",
                context_name="Acme",
                kind="organization",
            )
        ]
    )

    assert _FakeSMTP.instances[0].close_called is True
