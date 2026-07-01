"""Shared invitation email orchestration and inviter-display helpers.

Both the organization and workspace routers build the same invitation email
messages from a batch of :class:`BatchInviteItem` results and re-send the same
single-invitation email on resend. This module holds that construction once so
routers only decide transport (send in a background task) and response shape.
"""

from __future__ import annotations

from tracecat.db.models import User
from tracecat.email.client import InvitationEmail, build_accept_url
from tracecat.email.templates import InvitationKind
from tracecat.invitations.types import BatchInviteItem, BatchInviteStatus


def inviter_display_name_and_email(
    user: User | None,
) -> tuple[str | None, str | None]:
    """Build a (display name, email) pair for invitation inviter fields.

    Falls back to the email as the display name when the user has no first or
    last name. Returns ``(None, None)`` when there is no inviter.
    """
    if user is None:
        return None, None
    if user.first_name or user.last_name:
        name = " ".join(p for p in (user.first_name, user.last_name) if p)
    else:
        name = user.email
    return name, user.email


def build_created_invitation_emails(
    items: list[BatchInviteItem],
    *,
    context_name: str,
    kind: InvitationKind,
) -> list[InvitationEmail]:
    """Build invitation emails for the CREATED items of a bulk request.

    Only items with status CREATED and a token produce a message. The result is
    safe to hand to a background task: every field is a plain string resolved
    eagerly, so no database access is required at send time.
    """
    return [
        InvitationEmail(
            to=item.email,
            accept_url=build_accept_url(item.token),
            context_name=context_name,
            kind=kind,
        )
        for item in items
        if item.status == BatchInviteStatus.CREATED and item.token
    ]


def build_single_invitation_email(
    *,
    to: str,
    token: str,
    context_name: str,
    kind: InvitationKind,
) -> InvitationEmail:
    """Build a single invitation email (resend flow)."""
    return InvitationEmail(
        to=to,
        accept_url=build_accept_url(token),
        context_name=context_name,
        kind=kind,
    )
