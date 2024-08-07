"""Core email actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import os
import re
import smtplib
import ssl
from abc import abstractmethod
from email.message import EmailMessage
from typing import Any

from loguru import logger

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
    @property
    def smtp_config(self):
        return {
            "host": os.environ.get("SMTP_HOST", ""),
            "port": os.environ.get("SMTP_PORT", 25),
            "auth": os.environ.get("SMTP_AUTH", "0"),
            "username": os.environ.get("SMTP_USER", ""),
            "password": os.environ.get("SMTP_PASS", ""),
            "ssl": os.environ.get("SMTP_SSL", "0"),
            "tls": os.environ.get("SMTP_STARTTLS", "0"),
            "ignore_cert_errors": os.environ.get("SMTP_IGNORE_CERT_ERROR", "0"),
        }

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
            config = self.smtp_config
            if config["ssl"] == "1":
                context = None
                if config["ignore_cert_errors"] == "1":
                    context = ssl._create_unverified_context()
                server = smtplib.SMTP_SSL(
                    config["host"], config["port"], context=context
                )
            else:
                server = smtplib.SMTP(config["host"], config["port"])

            server.ehlo()

            if config["tls"] == "1":
                context = None
                if config["ignore_cert_errors"] == "1":
                    context = ssl._create_unverified_context()
                server.starttls(context=context)

            if config["auth"] == "1":
                server.login(config["username"], config["password"])

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
