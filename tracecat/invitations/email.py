"""Invitation email: rendering and message construction."""

from __future__ import annotations

from html import escape

from tracecat import config
from tracecat.email.transport import OutboundEmail


def render_invitation_email(
    *,
    accept_url: str,
    organization_name: str,
) -> tuple[str, str, str]:
    """Render an invitation subject plus HTML and plain-text bodies."""
    safe_name = escape(organization_name)
    safe_url = escape(accept_url, quote=True)
    logo_url = escape(
        f"{config.TRACECAT__PUBLIC_APP_URL.rstrip('/')}/icon.png", quote=True
    )
    header_safe_name = "".join(char for char in organization_name if char.isprintable())

    subject = f"Join {header_safe_name} on Tracecat"
    # Both invitation services set a 7-day expiry.
    body = (
        "Accept the invitation to join the organization and get started. "
        "If you don't have an account, you'll have to create one before "
        "accepting the invitation."
    )
    expiry = (
        "This invitation expires in 7 days. "
        "If you weren't expecting it, you can ignore this email."
    )
    html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#111114;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f7;padding:40px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#ffffff;border:1px solid #e6e6ea;border-radius:10px;">
            <tr><td style="padding:32px 32px 0 32px;"><img src="{logo_url}" width="36" height="36" alt="Tracecat" style="display:block;border-radius:9px;" /></td></tr>
            <tr><td style="padding:24px 32px 0 32px;font-size:22px;font-weight:600;line-height:1.25;letter-spacing:-0.01em;color:#111114;">Join {safe_name} on Tracecat</td></tr>
            <tr><td style="padding:12px 32px 0 32px;font-size:15px;line-height:1.55;color:#3c3c43;">{body}</td></tr>
            <tr><td style="padding:28px 32px 0 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr>
                <td bgcolor="#6f76e0" style="border-radius:6px;"><a href="{safe_url}" style="display:inline-block;padding:11px 20px;font-size:14px;font-weight:600;line-height:1.2;color:#ffffff;text-decoration:none;">Accept invitation</a></td>
              </tr></table>
            </td></tr>
            <tr><td style="padding:24px 32px 28px 32px;font-size:13px;line-height:1.5;color:#6e6e78;">{expiry}</td></tr>
            <tr><td style="padding:16px 32px 20px 32px;border-top:1px solid #e6e6ea;font-size:12px;line-height:1.5;color:#6e6e78;">If the button doesn't work, paste this link into your browser:<br /><a href="{safe_url}" style="color:#6f76e0;word-break:break-all;">{safe_url}</a></td></tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    text = f"""\
Join {organization_name} on Tracecat

{body}

Accept your invitation:
{accept_url}

{expiry}
"""
    return subject, html, text


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
    return OutboundEmail(to=(to,), subject=subject, html=html, text=text)
