"""Core email actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import os
import re
from abc import abstractmethod
from typing import Any, Literal

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.registry import registry

SAFE_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailBouncedError(Exception):
    pass


class EmailNotFoundError(Exception):
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


class ResendMailProvider(AsyncMailProvider):
    @property
    def api_headers(self):
        api_key = os.environ["RESEND_API_KEY"]
        api_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        return api_headers

    @retry(stop=stop_after_attempt(3), wait=wait_exponential())
    async def _get_email_status(self, client: httpx.AsyncClient, email_id: str):
        email_response = await client.get(
            f"https://api.resend.com/emails/{email_id}", headers=self.api_headers
        )
        email_response.raise_for_status()
        email_status = email_response.json().get("last_event")
        if email_status is None:
            raise Exception("Email status is None")
        return email_status

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
        api_headers = self.api_headers
        params = {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "text": body,
            "bcc": bcc,
            "cc": cc,
            "reply_to": reply_to,
            "headers": headers,
        }
        async with httpx.AsyncClient() as client:
            rsps = await client.post(
                "https://api.resend.com/emails", json=params, headers=api_headers
            )
            rsps.raise_for_status()
            email_id = rsps.json()["id"]
            email_status = await self._get_email_status(client, email_id)
            if email_status is None:
                raise EmailNotFoundError(
                    "Successfully posted email, but failed to find email in Resend after 3 retries."
                )
            elif email_status == "bounced":
                raise EmailBouncedError("Email bounced")

        return email_status


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Perform a send email action",
    secrets=["resend_api_key"],
)
async def send_email(
    recipients: list[str],
    subject: str,
    body: str,
    sender: str = "mail@tracecat.com",
    provider: Literal["resend"] = "resend",
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a send email action."""
    logger.debug(
        "Perform send email action",
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )

    if provider == "resend":
        email_provider = ResendMailProvider(
            sender=sender,
            recipients=recipients,
            subject=subject,
            body=body,
        )
    else:
        msg = "Email provider not recognized"
        logger.warning("{}: {!r}", msg, provider)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
        return email_response

    try:
        await email_provider.send()
    except httpx.HTTPError as exc:
        msg = "Failed to post email to provider"
        logger.opt(exception=exc).error(msg, exc_info=exc)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    except (EmailBouncedError, EmailNotFoundError) as e:
        msg = e.args[0]
        logger.opt(exception=e).warning(msg=msg, error=e)
        email_response = {
            "status": "warning",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    else:
        email_response = {
            "status": "ok",
            "message": "Successfully sent email",
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }

    return email_response
