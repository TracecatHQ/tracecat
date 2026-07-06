"""Provider-agnostic rendering for invitation emails."""

from __future__ import annotations

from html import escape
from typing import Literal

from tracecat import config

InvitationKind = Literal["organization", "workspace"]

# Neutral ink used for the wordmark, primary button, and logo tile, matching
# the dark rounded-square app icon and the "prefer neutral" UI house style.
_BRAND_INK = "#1a1a1a"
# Frontend theme primary (--primary: 231 58% 64% -> hsl(231, 58%, 64%)), used
# only as a link accent rather than a large fill.
_BRAND_PRIMARY = "#6f76e0"


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
    logo_url = escape(f"{config.TRACECAT__PUBLIC_APP_URL}/icon.png", quote=True)

    # Strip control chars to prevent subject header injection.
    header_safe_name = "".join(ch for ch in context_name if ch.isprintable())
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
  <body style="margin:0;padding:0;background:#fafafa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#3c3c43;-webkit-font-smoothing:antialiased;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:48px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="440" cellpadding="0" cellspacing="0" style="max-width:440px;background:#ffffff;border:1px solid #ebebeb;border-radius:12px;">
            <tr>
              <td style="padding:40px 40px 0 40px;">
                <img src="{logo_url}" width="28" height="28" alt="Tracecat" style="display:block;border-radius:7px;background:{_BRAND_INK};" />
              </td>
            </tr>
            <tr>
              <td style="padding:24px 40px 0 40px;font-size:15px;line-height:1.6;color:#3c3c43;">{intro}</td>
            </tr>
            <tr>
              <td style="padding:24px 40px 0 40px;">
                <a href="{safe_url}" style="display:inline-block;background:{_BRAND_INK};color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;padding:11px 22px;border-radius:8px;">Accept invitation</a>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 40px 40px 40px;font-size:13px;line-height:1.6;color:#8a8a91;">{hint}</td>
            </tr>
            <tr>
              <td style="padding:20px 40px;border-top:1px solid #f0f0f0;font-size:12px;line-height:1.6;color:#a1a1a8;">
                Button not working? Paste this link into your browser:<br />
                <a href="{safe_url}" style="color:{_BRAND_PRIMARY};word-break:break-all;">{safe_url}</a>
              </td>
            </tr>
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
