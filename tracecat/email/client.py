"""Provider-neutral outbound email delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from types import MappingProxyType
from typing import Annotated, Protocol

import aiosmtplib
from fastapi import BackgroundTasks, Depends

from tracecat import config
from tracecat.email.templates import render_invitation_email
from tracecat.exceptions import TracecatException
from tracecat.logger import logger


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    """A rendered email ready for transport delivery."""

    to: tuple[str, ...]
    subject: str
    html: str
    text: str
    from_addr: str
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Sends run after the response; detach from the caller's mapping.
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class EmailDeliveryError(TracecatException):
    """A send failed. Carries the error type only, never recipients or content."""


class EmailTransport(Protocol):
    """Provider-neutral delivery interface."""

    async def send(self, message: OutboundEmail) -> None: ...


class SMTPTransport:
    """SMTP transport that opens one connection per message."""

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password

    async def send(self, message: OutboundEmail) -> None:
        mime = EmailMessage()
        mime["From"] = message.from_addr
        mime["To"] = ", ".join(message.to)
        mime["Subject"] = message.subject
        for key, value in message.headers.items():
            mime[key] = value
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        # Port 465 is implicit TLS; everything else upgrades via STARTTLS.
        await aiosmtplib.send(
            mime,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            use_tls=self._port == 465,
            start_tls=self._port != 465,
        )


def is_email_configured() -> bool:
    """Return whether every required SMTP setting is present."""
    return bool(
        config.TRACECAT__SMTP_HOST
        and config.TRACECAT__SMTP_USER
        and config.TRACECAT__SMTP_PASSWORD
        and config.TRACECAT__EMAIL_DOMAIN
    )


def platform_from_addr() -> str:
    """Build the platform sender address from the verified sending domain."""
    return formataddr(("Tracecat", f"no-reply@{config.TRACECAT__EMAIL_DOMAIN}"))


def build_accept_url(token: str) -> str:
    """Build the public invitation acceptance URL."""
    return (
        f"{config.TRACECAT__PUBLIC_APP_URL.rstrip('/')}/invitations/accept"
        f"?token={token}"
    )


def invitation_email(*, to: str, organization_name: str, token: str) -> OutboundEmail:
    """Render an organization invitation into a deliverable message."""
    subject, html, text = render_invitation_email(
        accept_url=build_accept_url(token),
        context_name=organization_name,
    )
    return OutboundEmail(
        to=(to,),
        subject=subject,
        html=html,
        text=text,
        from_addr=platform_from_addr(),
    )


async def _send(message: OutboundEmail) -> None:
    transport = SMTPTransport(
        host=config.TRACECAT__SMTP_HOST or "",
        port=config.TRACECAT__SMTP_PORT,
        username=config.TRACECAT__SMTP_USER or "",
        password=config.TRACECAT__SMTP_PASSWORD or "",
    )
    try:
        await transport.send(message)
    except Exception as error:
        logger.error(
            "Failed to send email",
            host=config.TRACECAT__SMTP_HOST,
            port=config.TRACECAT__SMTP_PORT,
            error_type=type(error).__name__,
        )
        raise EmailDeliveryError(
            f"SMTP delivery failed: {type(error).__name__}"
        ) from None


class Mailer:
    """Queues sends after the response; routers never touch BackgroundTasks."""

    def __init__(self, tasks: BackgroundTasks) -> None:
        self._tasks = tasks

    def deliver(self, message: OutboundEmail) -> None:
        # ponytail: in-process send, swap for a Temporal activity when agent mail needs retries.
        if not is_email_configured():
            logger.debug("Email is not configured; dropping message")
            return
        self._tasks.add_task(_send, message)


def _get_mailer(tasks: BackgroundTasks) -> Mailer:
    return Mailer(tasks)


MailerDep = Annotated[Mailer, Depends(_get_mailer)]
