"""Service for managing model access control."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError

from tracecat.agent.access.schemas import AgentModelAccessRead
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentModelAccess
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.service import BaseOrgService


class AgentModelAccessService(BaseOrgService):
    """Manage model access for organizations and workspaces."""

    service_name = "agent_model_access"

    @require_scope("agent:create")
    @audit_log(resource_type="agent_model_access", action="create")
    async def enable_model(
        self,
        catalog_id: UUID,
        workspace_id: UUID | None = None,
    ) -> AgentModelAccessRead:
        """Enable a model for org or workspace.

        Args:
            catalog_id: Catalog entry ID to enable.
            workspace_id: Workspace ID, or ``None`` for org-level access.

        Returns:
            The created access entry.
        """
        access = AgentModelAccess(
            organization_id=self.organization_id,
            workspace_id=workspace_id,
            catalog_id=catalog_id,
        )
        self.session.add(access)
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                f"Model access for catalog {catalog_id} already enabled"
            ) from err
        await self.session.refresh(access)
        return AgentModelAccessRead.model_validate(access)

    @require_scope("agent:delete")
    @audit_log(
        resource_type="agent_model_access",
        action="delete",
        resource_id_attr="access_id",
    )
    async def disable_model(self, access_id: UUID) -> None:
        """Disable a model.

        Args:
            access_id: Access entry ID to delete.

        Raises:
            TracecatNotFoundError: The access row does not exist.
        """
        stmt = select(AgentModelAccess).where(
            and_(
                AgentModelAccess.id == access_id,
                AgentModelAccess.organization_id == self.organization_id,
            )
        )
        access = (await self.session.execute(stmt)).scalar_one_or_none()
        if access is None:
            raise TracecatNotFoundError(f"Access entry {access_id} not found")
        await self.session.delete(access)
        await self.session.commit()

    @require_scope("agent:read")
    async def list_enabled_models(
        self,
        workspace_id: UUID | None = None,
        cursor_params: CursorPaginationParams | None = None,
    ) -> tuple[list[AgentModelAccessRead], str | None]:
        """List enabled models with cursor pagination."""
        params = cursor_params or CursorPaginationParams(limit=50)
        conditions = [AgentModelAccess.organization_id == self.organization_id]
        if workspace_id is not None:
            conditions.append(AgentModelAccess.workspace_id == workspace_id)

        stmt = (
            select(AgentModelAccess)
            .where(and_(*conditions))
            .order_by(
                AgentModelAccess.created_at.desc(),
                AgentModelAccess.id.desc(),
            )
        )

        paginator = BaseCursorPaginator(self.session)
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
            except (ValueError, AttributeError) as err:
                raise ValueError("Invalid cursor") from err
            if not isinstance(cursor_data.sort_value, datetime):
                raise ValueError("Invalid cursor")
            stmt = stmt.where(
                sa.or_(
                    AgentModelAccess.created_at < cursor_data.sort_value,
                    sa.and_(
                        AgentModelAccess.created_at == cursor_data.sort_value,
                        AgentModelAccess.id < UUID(cursor_data.id),
                    ),
                )
            )

        result = await self.session.execute(stmt.limit(params.limit + 1))
        items = result.scalars().all()
        has_more = len(items) > params.limit
        if has_more:
            items = items[: params.limit]

        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = paginator.encode_cursor(
                last_item.id,
                sort_column="created_at",
                sort_value=last_item.created_at,
            )

        return [AgentModelAccessRead.model_validate(row) for row in items], next_cursor

    @require_scope("agent:read")
    async def get_org_models(self) -> list[AgentCatalogRead]:
        """Return catalog rows enabled for the organization."""
        stmt = (
            select(AgentCatalog)
            .join(AgentModelAccess, AgentModelAccess.catalog_id == AgentCatalog.id)
            .where(
                AgentModelAccess.organization_id == self.organization_id,
                AgentModelAccess.workspace_id.is_(None),
            )
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [AgentCatalogRead.model_validate(row) for row in rows]

    @require_scope("agent:read")
    async def get_workspace_models(self, workspace_id: UUID) -> list[AgentCatalogRead]:
        """Return the effective model set for a workspace.

        If the workspace has any explicit access rows, those fully override the
        org-level set. Otherwise the org-level set applies.
        """
        workspace_override_exists = await self.session.scalar(
            select(
                exists().where(
                    and_(
                        AgentModelAccess.organization_id == self.organization_id,
                        AgentModelAccess.workspace_id == workspace_id,
                    )
                )
            )
        )

        if workspace_override_exists:
            effective_catalog_ids = select(AgentModelAccess.catalog_id).where(
                and_(
                    AgentModelAccess.organization_id == self.organization_id,
                    AgentModelAccess.workspace_id == workspace_id,
                )
            )
        else:
            effective_catalog_ids = select(AgentModelAccess.catalog_id).where(
                and_(
                    AgentModelAccess.organization_id == self.organization_id,
                    AgentModelAccess.workspace_id.is_(None),
                )
            )

        stmt = (
            select(AgentCatalog)
            .where(AgentCatalog.id.in_(effective_catalog_ids))
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
            )
        )

        rows = (await self.session.execute(stmt)).scalars().all()
        return [AgentCatalogRead.model_validate(row) for row in rows]
