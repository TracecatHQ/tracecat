"""Provider-neutral outbound email transport. Knows nothing about what it sends."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from tracecat import config
from tracecat.exceptions import TracecatException


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    """A rendered email ready for transport delivery."""

    to: tuple[str, ...]
    subject: str
    html: str
    text: str


class EmailDeliveryError(TracecatException):
    """A send failed. Carries port and error type only, never host or recipients."""


@dataclass(frozen=True, slots=True)
class SMTPTransport:
    """SMTP transport that opens one connection per message."""

    host: str
    port: int
    username: str
    password: str
    from_addr: str

    @classmethod
    def from_config(cls) -> SMTPTransport | None:
        """Return the configured transport, or None while any SMTP setting is unset."""
        host = config.TRACECAT__SMTP_HOST
        username = config.TRACECAT__SMTP_USER
        password = config.TRACECAT__SMTP_PASSWORD
        from_addr = config.TRACECAT__EMAIL_FROM
        if not (host and username and password and from_addr):
            return None
        return cls(
            host=host,
            port=config.TRACECAT__SMTP_PORT,
            username=username,
            password=password,
            from_addr=from_addr,
        )

    async def send(self, message: OutboundEmail) -> None:
        """Deliver one message, raising `EmailDeliveryError` on any failure."""
        mime = EmailMessage()
        mime["From"] = self.from_addr
        mime["To"] = ", ".join(message.to)
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        try:
            # Port 465 is implicit TLS; everything else upgrades via STARTTLS.
            # A hung relay must fail inside the 30s orchestrator stop grace.
            await aiosmtplib.send(
                mime,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                use_tls=self.port == 465,
                start_tls=self.port != 465,
                timeout=20,
            )
        except Exception as error:
            # No host, recipients, or cause: relay responses may echo
            # customer infrastructure or addresses.
            raise EmailDeliveryError(
                f"SMTP delivery failed on port {self.port}: {type(error).__name__}"
            ) from None
