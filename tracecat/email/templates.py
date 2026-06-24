"""Provider-agnostic rendering for invitation emails."""

from __future__ import annotations

from html import escape
from typing import Literal

InvitationKind = Literal["organization", "workspace"]


def render_invitation_email(
    *,
    accept_url: str,
    context_name: str,
    kind: InvitationKind,
) -> tuple[str, str, str]:
    """Render an invitation email.

    Args:
        accept_url: The link the invitee follows to accept the invitation.
        context_name: The organization or workspace name.
        kind: Whether the invite is to an organization or a workspace.

    Returns:
        A ``(subject, html, text)`` tuple.
    """
    # context_name is admin-controlled (org/workspace name) and accept_url is
    # server-generated; escape both before interpolating into HTML to prevent
    # markup/link injection into trusted invitation emails.
    safe_name = escape(context_name)
    safe_url = escape(accept_url, quote=True)

    subject = f"Invitation to join '{context_name}' on Tracecat"
    intro = f"You've been invited to join <strong>{safe_name}</strong> on Tracecat."
    intro_text = f"You've been invited to join {context_name} on Tracecat."
    note = (
        "Click the button below to accept. If you don't have a Tracecat account "
        "yet, you'll be prompted to create one."
    )

    html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f6f6;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;padding:40px;">
            <tr><td style="font-size:24px;font-weight:600;padding-bottom:24px;">Tracecat</td></tr>
            <tr><td style="font-size:15px;line-height:1.6;padding-bottom:16px;">Hello,</td></tr>
            <tr><td style="font-size:15px;line-height:1.6;padding-bottom:16px;">{intro}</td></tr>
            <tr><td style="font-size:15px;line-height:1.6;padding-bottom:24px;">{note}</td></tr>
            <tr>
              <td style="padding-bottom:24px;">
                <a href="{safe_url}" style="display:inline-block;background:#6366f1;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:12px 28px;border-radius:8px;">Accept invitation</a>
              </td>
            </tr>
            <tr><td style="font-size:15px;line-height:1.6;padding-bottom:8px;">Regards,</td></tr>
            <tr><td style="font-size:15px;line-height:1.6;padding-bottom:24px;">Tracecat</td></tr>
            <tr>
              <td style="font-size:12px;line-height:1.6;color:#888888;border-top:1px solid #eeeeee;padding-top:16px;">
                If you're having trouble clicking the button, copy and paste this URL into your browser:<br />
                <a href="{safe_url}" style="color:#6366f1;word-break:break-all;">{safe_url}</a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    text = f"""\
Hello,

{intro_text}

{note}

Accept your invitation:
{accept_url}

Regards,
Tracecat
"""

    return subject, html, text
