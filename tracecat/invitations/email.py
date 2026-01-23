"""Invitation email utilities."""

import os

import resend

from tracecat import config


def send_invitation_email(
    *,
    to_email: str,
    org_name: str,
    token: str,
) -> str:
    """Send an invitation email via Resend.

    Args:
        to_email: Recipient email address.
        org_name: Name of the organization for the email content.
        token: The invitation token.

    Returns:
        The magic link URL.

    Raises:
        ValueError: If RESEND_API_KEY is not set.
        Exception: If sending fails.
    """
    resend_api_key = os.environ.get("RESEND_API_KEY")
    if not resend_api_key:
        raise ValueError("RESEND_API_KEY environment variable is not set")
    resend.api_key = resend_api_key

    from_email = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
    magic_link = f"{config.TRACECAT__PUBLIC_APP_URL}/invite/accept?token={token}"

    subject = f"You've been invited to join {org_name} on Tracecat"
    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Join {org_name} on Tracecat</h2>
        <p>You've been invited to join <strong>{org_name}</strong> on Tracecat.</p>
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

    You've been invited to join {org_name} on Tracecat.

    Accept the invitation by clicking this link:
    {magic_link}

    This invitation will expire in 7 days.
""".strip()

    resend.Emails.send(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
            "text": text_content,
        }
    )
    return magic_link
