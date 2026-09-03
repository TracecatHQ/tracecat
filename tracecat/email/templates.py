"""Provider-neutral invitation email rendering."""

from __future__ import annotations

from html import escape
from typing import Literal

from tracecat import config

InvitationKind = Literal["organization", "workspace"]


def render_invitation_email(
    *,
    accept_url: str,
    context_name: str,
    kind: InvitationKind,
) -> tuple[str, str, str]:
    """Render an invitation subject plus HTML and plain-text bodies."""
    safe_name = escape(context_name)
    safe_url = escape(accept_url, quote=True)
    logo_url = escape(f"{config.TRACECAT__PUBLIC_APP_URL}/icon.png", quote=True)
    header_safe_name = "".join(char for char in context_name if char.isprintable())

    label = "Workspace" if kind == "workspace" else "Organization"
    if kind == "workspace":
        subject = f"Join the {header_safe_name} workspace on Tracecat"
        intro = (
            "You've been invited to join the "
            f"<strong>{safe_name}</strong> workspace on Tracecat."
        )
        intro_text = (
            f"You've been invited to join the {context_name} workspace on Tracecat."
        )
    else:
        subject = f"Join {header_safe_name} on Tracecat"
        intro = f"You've been invited to join <strong>{safe_name}</strong> on Tracecat."
        intro_text = f"You've been invited to join {context_name} on Tracecat."

    hint = "If you don't have an account yet, you'll be prompted to create one."
    html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#0a0a0a;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:48px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="448" cellpadding="0" cellspacing="0" style="max-width:448px;background:#ffffff;border:1px solid #e4e4e7;border-radius:12px;">
            <tr><td align="center" style="padding:24px 24px 0 24px;"><img src="{logo_url}" width="64" height="64" alt="Tracecat" style="display:block;border-radius:16px;" /></td></tr>
            <tr><td align="center" style="padding:16px 24px 0 24px;font-size:20px;font-weight:600;line-height:1.2;letter-spacing:-0.01em;">You've been invited!</td></tr>
            <tr><td align="center" style="padding:6px 24px 0 24px;font-size:14px;line-height:1.5;color:#737373;">{intro}</td></tr>
            <tr><td style="padding:24px 24px 0 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;border-radius:8px;background:#fafafa;">
                <tr><td style="padding:16px;font-size:14px;line-height:1.5;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
                    <td style="color:#737373;">{label}</td>
                    <td align="right" style="font-weight:500;">{safe_name}</td>
                  </tr></table>
                </td></tr>
              </table>
            </td></tr>
            <tr><td style="padding:24px 24px 0 24px;"><a href="{safe_url}" style="display:block;background:#6f76e0;color:#ffffff;text-decoration:none;text-align:center;font-size:14px;font-weight:500;line-height:36px;border-radius:6px;">Accept invitation</a></td></tr>
            <tr><td align="center" style="padding:16px 24px 24px 24px;font-size:13px;line-height:1.5;color:#737373;">{hint}</td></tr>
            <tr><td style="padding:16px 24px;border-top:1px solid #e4e4e7;font-size:12px;line-height:1.5;color:#a3a3a3;">Button not working? Paste this link into your browser:<br /><a href="{safe_url}" style="color:#6f76e0;word-break:break-all;">{safe_url}</a></td></tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    text = f"""\
{intro_text}

Accept your invitation:
{accept_url}

{hint}
"""
    return subject, html, text
