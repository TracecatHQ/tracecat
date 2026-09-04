"""Provider-neutral outbound email delivery."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from typing import Annotated

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


class EmailDeliveryError(TracecatException):
    """A send failed. Carries port and error type only, never host or recipients."""


@dataclass(frozen=True, slots=True)
class SMTPTransport:
    """SMTP transport that opens one connection per message."""

    host: str
    port: int
    username: str
    password: str

    async def send(self, message: OutboundEmail) -> None:
        mime = EmailMessage()
        mime["From"] = message.from_addr
        mime["To"] = ", ".join(message.to)
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        # Port 465 is implicit TLS; everything else upgrades via STARTTLS.
        await aiosmtplib.send(
            mime,
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            use_tls=self.port == 465,
            start_tls=self.port != 465,
        )


def is_email_configured() -> bool:
    """Return whether every required SMTP setting is present."""
    return bool(
        config.TRACECAT__SMTP_HOST
        and config.TRACECAT__SMTP_USER
        and config.TRACECAT__SMTP_PASSWORD
        and config.TRACECAT__EMAIL_FROM
    )


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
        organization_name=organization_name,
    )
    return OutboundEmail(
        to=(to,),
        subject=subject,
        html=html,
        text=text,
        # Empty only when unconfigured; Mailer.deliver drops those.
        from_addr=config.TRACECAT__EMAIL_FROM or "",
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
        # Surfaces via Sentry/uvicorn. No host, recipients, or cause: relay
        # responses may echo customer infrastructure or addresses.
        raise EmailDeliveryError(
            f"SMTP delivery failed on port {config.TRACECAT__SMTP_PORT}: "
            f"{type(error).__name__}"
        ) from None


class Mailer:
    """Queues sends after the response; routers never touch BackgroundTasks."""

    def __init__(self, tasks: BackgroundTasks) -> None:
        self._tasks = tasks

    def deliver(self, message: OutboundEmail) -> None:
        # In-process send; swap for a Temporal activity when agent mail needs retries.
        if not is_email_configured():
            logger.debug("Email is not configured; dropping message")
            return
        self._tasks.add_task(_send, message)


def _get_mailer(tasks: BackgroundTasks) -> Mailer:
    return Mailer(tasks)


MailerDep = Annotated[Mailer, Depends(_get_mailer)]
