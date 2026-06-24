"""Resend-backed email client.

The Resend SDK is synchronous, so blocking calls are dispatched to a worker
thread via ``asyncio.to_thread``. All sends are best-effort: failures are logged
and never raised into the request, since invitations persist independently of
email delivery (the admin can resend or copy the invitation link).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import resend

from tracecat import config
from tracecat.email.templates import InvitationKind, render_invitation_email
from tracecat.logger import logger


@dataclass(slots=True)
class InvitationEmail:
    """A single invitation email to send."""

    to: str
    accept_url: str
    context_name: str
    kind: InvitationKind


def is_email_configured() -> bool:
    """Whether an email backend is configured at the platform level."""
    return bool(config.TRACECAT__RESEND_API_KEY and config.TRACECAT__RESEND_FROM_EMAIL)


def build_accept_url(token: str) -> str:
    """Build the invitation-accept URL for an invitation token."""
    return f"{config.TRACECAT__PUBLIC_APP_URL}/invitations/accept?token={token}"


def _build_params(message: InvitationEmail) -> resend.Emails.SendParams:
    subject, html, text = render_invitation_email(
        accept_url=message.accept_url,
        context_name=message.context_name,
        kind=message.kind,
    )
    return {
        "from": config.TRACECAT__RESEND_FROM_EMAIL or "",
        "to": [message.to],
        "subject": subject,
        "html": html,
        "text": text,
    }


def _send_sync(messages: list[InvitationEmail]) -> None:
    resend.api_key = config.TRACECAT__RESEND_API_KEY
    resend.Batch.send([_build_params(m) for m in messages])


async def send_invitation_emails_batch(messages: list[InvitationEmail]) -> None:
    """Send invitation emails in a single Resend batch call (best-effort).

    Failures are logged and swallowed so they never affect the request that
    created the invitations.
    """
    if not messages:
        return
    if not is_email_configured():
        logger.warning(
            "Skipping invitation emails: email is not configured",
            count=len(messages),
        )
        return

    try:
        await asyncio.to_thread(_send_sync, messages)
    except Exception:
        logger.exception(
            "Failed to send invitation emails via Resend",
            count=len(messages),
        )
