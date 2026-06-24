"""Email delivery for Tracecat.

Currently backed by Resend. When Resend is not configured, the platform falls
back to the copy-paste invitation link flow.
"""

from tracecat.email.client import (
    InvitationEmail,
    is_email_configured,
    send_invitation_email,
    send_invitation_emails_batch,
)
from tracecat.email.service import build_accept_url
from tracecat.email.templates import render_invitation_email

__all__ = [
    "InvitationEmail",
    "build_accept_url",
    "is_email_configured",
    "render_invitation_email",
    "send_invitation_email",
    "send_invitation_emails_batch",
]
