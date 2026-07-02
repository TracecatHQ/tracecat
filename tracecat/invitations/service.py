"""Shared implementation for bulk invitation upserts.

Both organization and workspace invitations follow the same batched-upsert
shape: normalize/dedup emails, bound the request size, pre-filter existing
members, canonicalize legacy mixed-case rows, then ``INSERT ... ON CONFLICT DO
UPDATE`` refreshing only stale invitations. This module holds that logic once;
the per-service callers supply the model, conflict target, member query, and any
extra insert columns.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Invitation, OrganizationInvitation
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.types import (
    MAX_BULK_INVITE_EMAILS,
    BatchInviteItem,
    BatchInviteStatus,
)

# The two concrete invitation models share the columns this module touches.
InvitationModel = type[Invitation] | type[OrganizationInvitation]


def _generate_batch_token() -> str:
    """Generate a unique token for a bulk-created invitation magic link."""
    return secrets.token_urlsafe(32)


async def batch_upsert_invitations(
    session: AsyncSession,
    *,
    model: InvitationModel,
    emails: list[str],
    role_id: uuid.UUID,
    invited_by: uuid.UUID | None,
    conflict_cols: list[str],
    scope_filter: ColumnElement[bool],
    member_email_stmt: Select[tuple[str]],
    member_skip_reason: str,
    extra_insert_values: dict[str, Any] | None = None,
) -> list[BatchInviteItem]:
    """Create invitations for many emails in one batched upsert.

    Emails are normalized (lowercased, stripped) and deduplicated. Existing
    members (as returned by ``member_email_stmt``) are skipped. The upsert
    refreshes only stale invitations (revoked/accepted/expired); a live pending
    invitation is left untouched.

    Args:
        session: Database session.
        model: The invitation model to upsert into.
        emails: Raw invitee emails (any case, possibly duplicated).
        role_id: RBAC role to assign upon acceptance.
        invited_by: User id of the inviter, or None for service-account actors.
        conflict_cols: The unique-constraint columns used as the ``ON CONFLICT``
            target (e.g. ``["workspace_id", "email"]``).
        scope_filter: Boolean predicate scoping canonicalization queries to the
            relevant workspace/organization (e.g. ``Invitation.workspace_id == x``).
        member_email_stmt: Select of lowercased member emails (already scoped and
            filtered to the normalized set) used to pre-skip existing members.
        member_skip_reason: Human-readable reason for the member-skip outcome.
        extra_insert_values: Extra columns to set on inserted rows (e.g. the
            org-only ``created_by_platform_admin`` flag).

    Returns:
        One :class:`BatchInviteItem` per distinct email, in input order.

    Raises:
        TracecatValidationError: If the request exceeds the bulk email limit.
        TracecatConflictError: If a constraint violation prevents the upsert.
    """
    # Defensive bound for direct (non-route) callers; the request schema
    # enforces the same limit at the API boundary.
    if len(emails) > MAX_BULK_INVITE_EMAILS:
        raise TracecatValidationError(
            f"Cannot invite more than {MAX_BULK_INVITE_EMAILS} emails at once"
        )

    # Normalize + dedup (case-insensitive, order-preserving).
    normalized = list(dict.fromkeys(e.strip().lower() for e in emails if e.strip()))
    if not normalized:
        return []

    # Pre-filter existing members (the case the invitation unique constraint
    # does not cover).
    member_result = await session.execute(member_email_stmt)
    existing_members = set(member_result.scalars().all())

    to_insert = [e for e in normalized if e not in existing_members]
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=7)

    email_col = model.email
    if to_insert:
        # Canonicalize legacy mixed-case rows before the upsert. The unique
        # constraint on the conflict columns is case-sensitive, so a pre-existing
        # row stored as e.g. "Foo@x.com" (created by the old single-invite path
        # before emails were normalized) would not match the ON CONFLICT target
        # for the normalized "foo@x.com" and would let the upsert insert a second
        # live pending invitation for the same address.
        #
        # Probe first and skip the entire delete/update block when no mixed-case
        # rows exist, so the common case pays only one cheap SELECT.
        mixed_case_result = await session.execute(
            select(email_col).where(
                scope_filter,
                email_col != func.lower(email_col),
                func.lower(email_col).in_(to_insert),
            )
        )
        has_mixed_case = mixed_case_result.first() is not None
        if has_mixed_case:
            # Guard against the pathological case where both a canonical row
            # ("foo@x.com") and a non-canonical row ("Foo@x.com") already exist:
            # lowercasing the latter would violate the unique constraint, so drop
            # the non-canonical duplicates that already have a canonical sibling
            # first.
            canonical_existing = await session.execute(
                select(email_col).where(scope_filter, email_col.in_(to_insert))
            )
            canonical_emails = set(canonical_existing.scalars().all())
            if canonical_emails:
                await session.execute(
                    delete(model).where(
                        scope_filter,
                        email_col != func.lower(email_col),
                        func.lower(email_col).in_(canonical_emails),
                    )
                )
            duplicate_canonical_rows = (
                select(
                    func.min(email_col).label("canonical_survivor"),
                    func.lower(email_col).label("canonical_email"),
                )
                .where(
                    scope_filter,
                    email_col != func.lower(email_col),
                    func.lower(email_col).in_(to_insert),
                )
                .group_by(func.lower(email_col))
                .having(func.count() > 1)
                .subquery()
            )
            await session.execute(
                delete(model).where(
                    scope_filter,
                    email_col != func.lower(email_col),
                    func.lower(email_col).in_(
                        select(duplicate_canonical_rows.c.canonical_email)
                    ),
                    email_col.not_in(
                        select(duplicate_canonical_rows.c.canonical_survivor)
                    ),
                )
            )
            await session.execute(
                update(model)
                .where(
                    scope_filter,
                    func.lower(email_col).in_(to_insert),
                    email_col != func.lower(email_col),
                )
                .values(email=func.lower(email_col))
            )

    upserted: dict[str, tuple[uuid.UUID, str]] = {}
    if to_insert:
        base_row: dict[str, Any] = dict(extra_insert_values or {})
        # The scope column(s) beyond "email" live in extra_insert_values (e.g.
        # workspace_id / organization_id), so callers pass them there.
        values = [
            {
                "id": uuid.uuid4(),
                "email": email,
                "role_id": role_id,
                "invited_by": invited_by,
                "token": _generate_batch_token(),
                "status": InvitationStatus.PENDING,
                "expires_at": expires_at,
                **base_row,
            }
            for email in to_insert
        ]
        stmt = (
            pg_insert(model)
            .values(values)
            .on_conflict_do_update(
                index_elements=conflict_cols,
                set_={
                    "role_id": role_id,
                    "invited_by": invited_by,
                    "token": pg_insert(model).excluded.token,
                    "status": InvitationStatus.PENDING,
                    "expires_at": expires_at,
                    "accepted_at": None,
                },
                where=(
                    (model.status != InvitationStatus.PENDING)
                    | (model.expires_at <= now)
                ),
            )
            .returning(model.id, model.email, model.token)
        )
        try:
            result = await session.execute(stmt)
            for inv_id, email, token in result.all():
                upserted[email] = (inv_id, token)
            await session.commit()
        except IntegrityError as e:
            # Translate constraint violations into a domain conflict error.
            await session.rollback()
            raise TracecatConflictError(
                "Could not create invitations due to a conflicting "
                "invitation. Please retry."
            ) from e

    items: list[BatchInviteItem] = []
    for email in normalized:
        if email in existing_members:
            items.append(
                BatchInviteItem(
                    email=email,
                    status=BatchInviteStatus.SKIPPED,
                    reason=member_skip_reason,
                )
            )
        elif email in upserted:
            inv_id, token = upserted[email]
            items.append(
                BatchInviteItem(
                    email=email,
                    status=BatchInviteStatus.CREATED,
                    invitation_id=inv_id,
                    token=token,
                )
            )
        else:
            items.append(
                BatchInviteItem(
                    email=email,
                    status=BatchInviteStatus.SKIPPED,
                    reason="A pending invitation already exists",
                )
            )
    return items
