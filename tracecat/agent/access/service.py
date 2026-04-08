"""Service for managing model access control."""

from uuid import UUID

from sqlalchemy import and_, exists, select

from tracecat.agent.access.schemas import AgentModelAccessRead
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.db.models import AgentCatalog, AgentModelAccess
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.service import BaseOrgService


class AgentModelAccessService(BaseOrgService):
    """Manage model access for organizations and workspaces."""

    service_name = "agent_model_access"

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
        await self.session.commit()
        return AgentModelAccessRead.model_validate(access)

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
            .order_by(AgentModelAccess.created_at.desc())
        )

        paginator = BaseCursorPaginator(self.session)
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
            except (ValueError, AttributeError) as err:
                raise ValueError("Invalid cursor") from err
            stmt = stmt.where(AgentModelAccess.created_at < cursor_data.sort_value)

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

    async def get_workspace_models(self, workspace_id: UUID) -> list[AgentCatalogRead]:
        """Return the effective model set for a workspace."""
        org_enabled_catalog_ids = select(AgentModelAccess.catalog_id).where(
            and_(
                AgentModelAccess.organization_id == self.organization_id,
                AgentModelAccess.workspace_id.is_(None),
            )
        )
        workspace_enabled_catalog_ids = select(AgentModelAccess.catalog_id).where(
            and_(
                AgentModelAccess.organization_id == self.organization_id,
                AgentModelAccess.workspace_id == workspace_id,
            )
        )
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

        stmt = (
            select(AgentCatalog)
            .where(AgentCatalog.id.in_(org_enabled_catalog_ids))
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
            )
        )
        if workspace_override_exists:
            stmt = stmt.where(AgentCatalog.id.in_(workspace_enabled_catalog_ids))

        rows = (await self.session.execute(stmt)).scalars().all()
        return [AgentCatalogRead.model_validate(row) for row in rows]
