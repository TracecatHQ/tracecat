"""Provider-neutral outbound email delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Protocol, Self

import aiosmtplib
from pydantic import EmailStr, TypeAdapter, ValidationError

from tracecat import config
from tracecat.email.templates import InvitationKind, render_invitation_email
from tracecat.logger import logger

_EMAIL_ADDRESS_ADAPTER = TypeAdapter(EmailStr)


@dataclass(frozen=True, slots=True)
class InvitationEmail:
    """An invitation email awaiting rendering."""

    to: str
    accept_url: str
    context_name: str
    kind: InvitationKind


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    """A rendered email ready for transport delivery."""

    to: tuple[str, ...]
    subject: str
    html: str
    text: str
    from_addr: str
    headers: Mapping[str, str] = field(default_factory=dict)


class EmailTransport(Protocol):
    """Provider-neutral delivery interface."""

    async def send(self, message: OutboundEmail) -> None: ...


class SMTPTransport:
    """SMTP transport backed by a reusable ``aiosmtplib`` connection."""

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client: aiosmtplib.SMTP | None = None

    async def __aenter__(self) -> Self:
        use_tls = self._port == 465
        client = aiosmtplib.SMTP(
            hostname=self._host,
            port=self._port,
            use_tls=use_tls,
            start_tls=not use_tls,
        )
        try:
            await client.connect()
            await client.login(self._username, self._password)
        except BaseException:
            client.close()
            raise
        self._client = client
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if self._client is not None:
            try:
                await self._client.quit()
            finally:
                self._client = None

    async def send(self, message: OutboundEmail) -> None:
        """Send one message over the open SMTP connection."""
        if self._client is None:
            raise RuntimeError("SMTP transport is not connected")

        sender_name, sender_email = parseaddr(message.from_addr)
        mime = EmailMessage()
        mime["From"] = (
            formataddr((sender_name, sender_email)) if sender_name else sender_email
        )
        mime["To"] = ", ".join(message.to)
        mime["Subject"] = message.subject
        for key, value in message.headers.items():
            mime[key] = value
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        await self._client.send_message(
            mime,
            sender=sender_email,
            recipients=list(message.to),
        )


def is_email_configured() -> bool:
    """Return whether all required SMTP settings contain valid values."""
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
    """Build the public invitation acceptance URL."""
    return (
        f"{config.TRACECAT__PUBLIC_APP_URL.rstrip('/')}/invitations/accept"
        f"?token={token}"
    )


def _render_invitation(message: InvitationEmail) -> OutboundEmail:
    subject, html, text = render_invitation_email(
        accept_url=message.accept_url,
        context_name=message.context_name,
        kind=message.kind,
    )
    return OutboundEmail(
        to=(message.to,),
        subject=subject,
        html=html,
        text=text,
        from_addr=config.TRACECAT__EMAIL_FROM or "",
    )


async def send_emails(
    messages: Sequence[OutboundEmail], transport: EmailTransport
) -> None:
    """Send messages serially without propagating per-message failures."""
    for index, message in enumerate(messages):
        try:
            await transport.send(message)
        except Exception as error:
            logger.error(
                "Failed to send email",
                error_type=type(error).__name__,
                message_index=index,
                message_count=len(messages),
            )


async def send_invitation_emails(messages: Sequence[InvitationEmail]) -> None:
    """Render and send invitation emails using the configured SMTP relay."""
    if not messages or not is_email_configured():
        return

    transport = SMTPTransport(
        host=config.TRACECAT__SMTP_HOST or "",
        port=config.TRACECAT__SMTP_PORT,
        username=config.TRACECAT__SMTP_USER or "",
        password=config.TRACECAT__SMTP_PASSWORD or "",
    )
    try:
        async with transport:
            await send_emails(
                [_render_invitation(message) for message in messages],
                transport,
            )
    except Exception as error:
        logger.error(
            "Failed to connect to SMTP relay",
            error_type=type(error).__name__,
            message_count=len(messages),
        )
