"""Effective model access: the catalog rows a workspace may use."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Select, select
from sqlalchemy.orm import aliased

from tracecat.db.models import AgentModelAccess


def effective_catalog_ids(
    organization_id: UUID, workspace_id: UUID | None
) -> Select[tuple[UUID]]:
    """Select the catalog IDs enabled for a workspace, or for the org when None.

    The org-level rows (``workspace_id IS NULL``) are both the default set and
    the upper bound. A workspace with access rows of its own uses only those
    the org still enables, so an org-level disable reaches every workspace. A
    workspace whose own rows are all disabled gets no models, not the org set.
    Callers still restrict catalog rows to those the org can see.
    """
    # Fresh aliases keep each subquery from correlating to a caller's
    # enclosing AgentModelAccess select.
    org_access = aliased(AgentModelAccess)
    org_enabled = select(org_access.catalog_id).where(
        org_access.organization_id == organization_id,
        org_access.workspace_id.is_(None),
    )
    if workspace_id is None:
        return org_enabled

    override = aliased(AgentModelAccess)
    has_override = (
        select(override.catalog_id)
        .where(
            override.organization_id == organization_id,
            override.workspace_id == workspace_id,
        )
        .exists()
    )
    access = aliased(AgentModelAccess)
    return select(access.catalog_id).where(
        access.organization_id == organization_id,
        sa.or_(
            sa.and_(
                has_override,
                access.workspace_id == workspace_id,
                access.catalog_id.in_(org_enabled),
            ),
            sa.and_(~has_override, access.workspace_id.is_(None)),
        ),
    )
