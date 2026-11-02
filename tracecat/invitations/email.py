"""Invitation email: rendering and message construction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from tracecat import config
from tracecat.email.transport import OutboundEmail

ORG_SCOPE_LABEL = "Organization-wide"


@dataclass(frozen=True, slots=True)
class InvitationGrantLine:
    """One grant rendered in the email: a NULL workspace is the org scope."""

    workspace_name: str | None
    role_name: str


def _render_grant_lines(
    grants: Sequence[InvitationGrantLine],
) -> tuple[str, str]:
    """Render the grants list as an HTML block and its plain-text equivalent."""
    if not grants:
        return "", ""
    scopes = [grant.workspace_name or ORG_SCOPE_LABEL for grant in grants]
    html_items = "".join(
        f'<tr><td style="padding:0 0 6px 0;font-size:14px;line-height:1.5;color:#3c3c43;">'
        f"<strong>{escape(scope)}</strong> &middot; {escape(grant.role_name)}</td></tr>"
        for scope, grant in zip(scopes, grants, strict=True)
    )
    html = (
        '<tr><td style="padding:20px 32px 0 32px;">'
        '<div style="font-size:13px;font-weight:600;color:#6e6e78;padding-bottom:8px;">'
        "Your access</div>"
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f"{html_items}</table></td></tr>"
    )
    text_items = "\n".join(
        f"- {scope}: {grant.role_name}"
        for scope, grant in zip(scopes, grants, strict=True)
    )
    text = f"Your access:\n{text_items}\n"
    return html, text


def render_invitation_email(
    *,
    accept_url: str,
    organization_name: str,
    grants: Sequence[InvitationGrantLine] = (),
) -> tuple[str, str, str]:
    """Render an invitation subject plus HTML and plain-text bodies."""
    safe_name = escape(organization_name)
    safe_url = escape(accept_url, quote=True)
    logo_url = escape(
        f"{config.TRACECAT__PUBLIC_APP_URL.rstrip('/')}/icon.png", quote=True
    )
    header_safe_name = "".join(char for char in organization_name if char.isprintable())

    subject = f"Join {header_safe_name} on Tracecat"
    body = (
        "Accept the invitation to join the organization and get started. "
        "If you don't have an account, you'll have to create one before "
        "accepting the invitation."
    )
    footer = "If you weren't expecting it, you can ignore this email."
    grants_html, grants_text = _render_grant_lines(grants)
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
            {grants_html}
            <tr><td style="padding:28px 32px 0 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr>
                <td bgcolor="#6f76e0" style="border-radius:6px;"><a href="{safe_url}" style="display:inline-block;padding:11px 20px;font-size:14px;font-weight:600;line-height:1.2;color:#ffffff;text-decoration:none;">Accept invitation</a></td>
              </tr></table>
            </td></tr>
            <tr><td style="padding:24px 32px 28px 32px;font-size:13px;line-height:1.5;color:#6e6e78;">{footer}</td></tr>
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

{grants_text}
Accept your invitation:
{accept_url}

{footer}
"""
    return subject, html, text


def build_accept_url(token: str) -> str:
    """Build the public invitation acceptance URL."""
    return (
        f"{config.TRACECAT__PUBLIC_APP_URL.rstrip('/')}/invitations/accept"
        f"?token={token}"
    )


def invitation_email(
    *,
    to: str,
    organization_name: str,
    token: str,
    grants: Sequence[InvitationGrantLine] = (),
) -> OutboundEmail:
    """Render an organization invitation into a deliverable message."""
    subject, html, text = render_invitation_email(
        accept_url=build_accept_url(token),
        organization_name=organization_name,
        grants=grants,
    )
    return OutboundEmail(to=(to,), subject=subject, html=html, text=text)
