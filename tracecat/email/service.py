"""Email service using Resend."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import resend

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "noreply@tracecat.com")


@dataclass
class EmailResult:
    """Result of sending an email."""

    success: bool
    message_id: str | None = None
    error: str | None = None


class EmailService:
    """Service for sending emails via Resend."""

    def __init__(self) -> None:
        if not RESEND_API_KEY:
            raise ValueError("RESEND_API_KEY environment variable is not set")
        resend.api_key = RESEND_API_KEY

    def send_org_invitation(
        self,
        *,
        to_email: str,
        org_name: str,
        magic_link: str,
        inviter_email: str | None = None,
    ) -> EmailResult:
        """Send an organization invitation email.

        Args:
            to_email: Recipient email address.
            org_name: Name of the organization.
            magic_link: The magic link URL for accepting the invitation.
            inviter_email: Email of the person who sent the invitation.

        Returns:
            EmailResult with success status and message ID or error.
        """
        subject = f"You've been invited to join {org_name} on Tracecat"

        inviter_line = (
            f"{inviter_email} has invited you"
            if inviter_email
            else "You've been invited"
        )

        html_content = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>Join {org_name} on Tracecat</h2>
            <p>{inviter_line} to join <strong>{org_name}</strong> on Tracecat.</p>
            <p>Click the button below to accept the invitation:</p>
            <p style="margin: 24px 0;">
                <a href="{magic_link}"
                   style="background-color: #000; color: #fff; padding: 12px 24px;
                          text-decoration: none; border-radius: 6px; display: inline-block;">
                    Accept Invitation
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                Or copy and paste this link into your browser:<br>
                <a href="{magic_link}" style="color: #666;">{magic_link}</a>
            </p>
            <p style="color: #666; font-size: 14px; margin-top: 24px;">
                This invitation will expire in 7 days.
            </p>
        </div>
        """

        text_content = f"""
Join {org_name} on Tracecat

{inviter_line} to join {org_name} on Tracecat.

Accept the invitation by clicking this link:
{magic_link}

This invitation will expire in 7 days.
        """.strip()

        try:
            response = resend.Emails.send(
                {
                    "from": RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                    "text": text_content,
                }
            )
            logger.info(
                "Invitation email sent to %s, message_id=%s", to_email, response["id"]
            )
            return EmailResult(success=True, message_id=response["id"])
        except Exception as e:
            logger.error("Failed to send invitation email to %s: %s", to_email, e)
            return EmailResult(success=False, error=str(e))
