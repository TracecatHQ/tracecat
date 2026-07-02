"""Provider-neutral SMTP email client.

Sends are dispatched over a generic SMTP transport (``aiosmtplib``), so the
provider (Resend, SES, a self-hosted MTA, ...) is a pure configuration swap
rather than a code change. All sends are best-effort: failures are logged and
never raised into the request, since invitations persist independently of email
delivery (the admin can resend or copy the invitation link).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Protocol

import aiosmtplib
from pydantic import EmailStr, TypeAdapter, ValidationError

from tracecat import config
from tracecat.email.templates import InvitationKind, render_invitation_email
from tracecat.logger import logger

_EMAIL_ADDRESS_ADAPTER = TypeAdapter(EmailStr)


@dataclass(slots=True)
class InvitationEmail:
    """A single invitation email to send."""

    to: str
    accept_url: str
    context_name: str
    kind: InvitationKind


@dataclass(slots=True)
class OutboundEmail:
    """A rendered, transport-ready email message."""

    to: list[str]
    subject: str
    html: str
    text: str
    from_addr: str
    headers: dict[str, str] = field(default_factory=dict)


class EmailTransport(Protocol):
    async def send(self, msg: OutboundEmail) -> None: ...


class SMTPTransport:
    """SMTP transport backed by ``aiosmtplib``.

    A single transport instance manages one connection; open it with
    ``connect()`` (or use it as an async context manager) to reuse the same
    connection across multiple ``send`` calls.
    """

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client: aiosmtplib.SMTP | None = None

    async def __aenter__(self) -> SMTPTransport:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the SMTP connection, negotiate STARTTLS, and authenticate.

        Port 465 uses implicit TLS; any other port (default 587) uses STARTTLS.
        """
        use_tls = self._port == 465
        client = aiosmtplib.SMTP(
            hostname=self._host,
            port=self._port,
            use_tls=use_tls,
            start_tls=not use_tls,
        )
        await client.connect()
        await client.login(self._username, self._password)
        self._client = client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.quit()
            finally:
                self._client = None

    async def send(self, msg: OutboundEmail) -> None:
        if self._client is None:
            raise RuntimeError("SMTPTransport.send called before connect()")

        mime = MIMEMultipart("alternative")
        # A display name may be embedded in from_addr (e.g. "Name <a@b.com>");
        # normalize it and use the bare address as the SMTP envelope sender.
        sender_name, sender_email = parseaddr(msg.from_addr)
        mime["From"] = (
            formataddr((sender_name, sender_email)) if sender_name else sender_email
        )
        mime["To"] = ", ".join(msg.to)
        mime["Subject"] = msg.subject
        for key, value in msg.headers.items():
            mime[key] = value
        mime.attach(MIMEText(msg.text, "plain", "utf-8"))
        mime.attach(MIMEText(msg.html, "html", "utf-8"))

        await self._client.send_message(mime, sender=sender_email, recipients=msg.to)


def is_email_configured() -> bool:
    """Whether an email backend is configured at the platform level."""
    if not (
        config.TRACECAT__SMTP_HOST
        and config.TRACECAT__SMTP_USER
        and config.TRACECAT__SMTP_PASSWORD
        and config.TRACECAT__EMAIL_FROM
    ):
        return False

    _, sender_email = parseaddr(config.TRACECAT__EMAIL_FROM)
    try:
        _EMAIL_ADDRESS_ADAPTER.validate_python(sender_email)
    except ValidationError:
        return False
    return True


def build_accept_url(token: str) -> str:
    """Build the invitation-accept URL for an invitation token."""
    return f"{config.TRACECAT__PUBLIC_APP_URL}/invitations/accept?token={token}"


def _build_outbound(message: InvitationEmail) -> OutboundEmail:
    subject, html, text = render_invitation_email(
        accept_url=message.accept_url,
        context_name=message.context_name,
        kind=message.kind,
    )
    return OutboundEmail(
        to=[message.to],
        subject=subject,
        html=html,
        text=text,
        from_addr=config.TRACECAT__EMAIL_FROM or "",
    )


async def send_invitation_emails_batch(messages: list[InvitationEmail]) -> None:
    """Send invitation emails over a single SMTP connection (best-effort).

    Opens one connection and sends each message sequentially. A per-message
    failure is logged and skipped so one bad recipient does not stop the rest.
    Failures never propagate into the request that created the invitations.
    """
    if not messages:
        return
    if not is_email_configured():
        logger.warning(
            "Skipping invitation emails: email is not configured",
            count=len(messages),
        )
        return

    transport = SMTPTransport(
        host=config.TRACECAT__SMTP_HOST or "",
        port=config.TRACECAT__SMTP_PORT,
        username=config.TRACECAT__SMTP_USER or "",
        password=config.TRACECAT__SMTP_PASSWORD or "",
    )

    try:
        async with transport:
            for index, message in enumerate(messages):
                try:
                    await transport.send(_build_outbound(message))
                except Exception:
                    logger.exception(
                        "Failed to send invitation email",
                        message_index=index,
                        count=len(messages),
                    )
    except Exception:
        logger.exception(
            "Failed to send invitation emails via SMTP",
            count=len(messages),
        )
