"""Core email actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import re
import smtplib
import ssl
from abc import abstractmethod
from email.message import EmailMessage
from typing import Any

from loguru import logger

from tracecat.config import (
    SMTP_AUTH_ENABLED,
    SMTP_HOST,
    SMTP_IGNORE_CERT_ERRORS,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_SSL_ENABLED,
    SMTP_STARTTLS_ENABLED,
    SMTP_USER,
)
from tracecat.registry import registry

SAFE_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailBouncedError(Exception):
    pass


class EmailNotFoundError(Exception):
    pass


class SmtpException(Exception):
    pass


class AsyncMailProvider:
    def __init__(
        self,
        sender: str,
        recipients: str | list[str],
        subject: str,
        body: str | None = None,
        bcc: str | list[str] | None = None,
        cc: str | list[str] | None = None,
        reply_to: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.sender = sender
        self.recipients = recipients
        self.subject = subject
        self.body = body
        self.bcc = bcc
        self.cc = cc
        self.reply_to = reply_to
        self.headers = headers

    @abstractmethod
    async def _send(
        self,
        sender: str,
        recipients: str | list[str],
        subject: str,
        body: str | None = None,
        bcc: str | list[str] | None = None,
        cc: str | list[str] | None = None,
        reply_to: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        pass

    async def send(self):
        await self._send(
            sender=self.sender,
            recipients=self.recipients,
            subject=self.subject,
            body=self.body,
            bcc=self.bcc,
            cc=self.cc,
            reply_to=self.reply_to,
            headers=self.headers,
        )


class SmtpMailProvider(AsyncMailProvider):
    async def _send(
        self,
        sender: str,
        recipients: str | list[str],
        subject: str,
        body: str | None = None,
        bcc: str | list[str] | None = None,
        cc: str | list[str] | None = None,
        reply_to: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        msg = EmailMessage()
        msg.set_content(body)
        msg["From"] = sender
        if type(recipients) is list:
            msg["To"] = ",".join(recipients)
        else:
            msg["To"] = recipients
        msg["Subject"] = subject
        if bcc is not None:
            msg["Bcc"] = bcc
        if cc is not None:
            msg["Cc"] = cc
        if reply_to is not None:
            msg["Reply-To"] = reply_to

        try:
            if SMTP_SSL_ENABLED:
                context = None
                if SMTP_IGNORE_CERT_ERRORS:
                    context = ssl._create_unverified_context()
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)

            server.ehlo()

            if SMTP_STARTTLS_ENABLED:
                context = None
                if SMTP_IGNORE_CERT_ERRORS:
                    context = ssl._create_unverified_context()
                server.starttls(context=context)

            if SMTP_AUTH_ENABLED:
                server.login(SMTP_USER, SMTP_PASS)

            # Send the email
            server.send_message(msg)
            server.quit()
        except Exception as e:
            raise SmtpException("Could not send email") from e

        return None


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Perform a send email action using SMTP",
    default_title="Send Email (SMTP)",
)
async def send_email_smtp(
    recipients: list[str],
    subject: str,
    body: str,
    sender: str = "mail@tracecat.com",
) -> dict[str, Any]:
    """Run a send email action."""
    logger.debug(
        "Perform send email (SMTP) action",
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )

    email_provider = SmtpMailProvider(
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )

    try:
        await email_provider.send()
    except SmtpException as smtp_e:
        msg = "Failed to send email"
        logger.opt(exception=smtp_e).error(msg, exc_info=smtp_e)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": "smtp",
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    except Exception as e:
        msg = "Failed to send email"
        logger.opt(exception=e).error(msg, exc_info=e)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": "smtp",
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    else:
        email_response = {
            "status": "ok",
            "message": "Successfully sent email",
            "provider": "smtp",
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }

    return email_response
