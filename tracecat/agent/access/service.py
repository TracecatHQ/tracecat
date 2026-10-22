"""Service for managing model access control."""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from asyncpg import ForeignKeyViolationError
from sqlalchemy import Select, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.agent.access.effective import effective_catalog_ids
from tracecat.agent.access.schemas import (
    AgentModelAccessRead,
    WorkspaceAgentModelAccessRead,
    WorkspaceAgentModelAccessUpdate,
)
from tracecat.agent.catalog.schemas import (
    AgentCatalogRead,
    WorkspaceAgentModelCandidate,
    WorkspaceAgentModelListResponse,
    WorkspaceAgentModelRead,
)
from tracecat.agent.default_model import resolve_org_default_model
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.errors import get_postgres_error, is_unique_violation
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Workspace,
)
from tracecat.db.types import PostgresConstraintError
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
        catalog_exists = await self.session.scalar(
            select(
                exists().where(
                    AgentCatalog.id == catalog_id,
                    sa.or_(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.organization_id.is_(None),
                    ),
                )
            )
        )
        if not catalog_exists:
            raise TracecatNotFoundError(f"Catalog {catalog_id} not found")

        if workspace_id is not None:
            workspace_exists = await self.session.scalar(
                select(
                    exists().where(
                        Workspace.id == workspace_id,
                        Workspace.organization_id == self.organization_id,
                    )
                )
            )
            if not workspace_exists:
                raise TracecatNotFoundError(f"Workspace {workspace_id} not found")

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
            if is_unique_violation(
                err, "uq_agent_model_access_organization_workspace_catalog"
            ):
                raise TracecatValidationError(
                    f"Model access for catalog {catalog_id} already enabled"
                ) from err
            foreign_key_error = get_postgres_error(err, ForeignKeyViolationError)
            if foreign_key_error is not None and cast(
                PostgresConstraintError, foreign_key_error
            ).constraint_name in {
                "fk_agent_model_access_catalog_id_agent_catalog",
                "fk_agent_model_access_workspace_id_workspace",
            }:
                raise TracecatNotFoundError(
                    f"Catalog {catalog_id} or workspace {workspace_id} not found"
                ) from err
            raise
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
            AgentModelAccess.id == access_id,
            AgentModelAccess.organization_id == self.organization_id,
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
            .where(*conditions)
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
                sa.or_(
                    AgentCatalog.organization_id == self.organization_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [AgentCatalogRead.model_validate(row) for row in rows]

    @require_scope("agent:read")
    async def get_workspace_models(
        self, workspace_id: UUID
    ) -> list[WorkspaceAgentModelRead]:
        """Return the effective model set for a workspace.

        A workspace subset narrows the org-enabled set and never extends it;
        see ``effective_catalog_ids``.
        """
        return await self._list_labelled_models(
            effective_catalog_ids(self.organization_id, workspace_id)
        )

    @require_scope("agent:read")
    async def list_workspace_models(
        self, workspace_id: UUID
    ) -> WorkspaceAgentModelListResponse:
        """Return a workspace's models and the org default, if it can use it."""
        models = await self.get_workspace_models(workspace_id)
        org_default = await resolve_org_default_model(
            self.session, self.organization_id
        )
        enabled_catalog_ids = {model.id for model in models}
        default_catalog_id = None
        if org_default is not None and org_default.id in enabled_catalog_ids:
            default_catalog_id = org_default.id
        subset_disabled = not models and bool(
            await self.session.scalar(
                select(
                    exists().where(
                        AgentModelAccess.organization_id == self.organization_id,
                        AgentModelAccess.workspace_id == workspace_id,
                    )
                )
            )
        )
        return WorkspaceAgentModelListResponse(
            items=models,
            default_catalog_id=default_catalog_id,
            has_org_default=org_default is not None,
            workspace_subset_disabled=subset_disabled,
        )

    @require_scope("agent:read")
    async def get_workspace_model_access(
        self, workspace_id: UUID
    ) -> WorkspaceAgentModelAccessRead:
        """Return a workspace's model subset and the org models it can pick from."""
        return await self._read_workspace_model_access(workspace_id)

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_model_access",
        action="update",
        resource_id_attr="workspace_id",
    )
    async def set_workspace_model_access(
        self,
        workspace_id: UUID,
        params: WorkspaceAgentModelAccessUpdate,
    ) -> WorkspaceAgentModelAccessRead:
        """Replace a workspace's model subset, or clear it to inherit the org set.

        Raises:
            TracecatValidationError: A catalog ID is not enabled for the
                organization.
        """
        # Serialize replacements of one workspace's subset, so concurrent saves
        # cannot interleave into the union of both. NO KEY UPDATE still lets
        # other writers insert rows that reference the workspace.
        await self.session.execute(
            select(Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.organization_id == self.organization_id,
            )
            .with_for_update(key_share=True)
        )
        desired_catalog_ids = set(params.catalog_ids)
        if desired_catalog_ids:
            candidates = await self._list_labelled_models(
                effective_catalog_ids(self.organization_id, None)
            )
            candidate_ids = {candidate.id for candidate in candidates}
            if unknown := desired_catalog_ids - candidate_ids:
                raise TracecatValidationError(
                    "Models not enabled for the organization: "
                    + ", ".join(sorted(str(catalog_id) for catalog_id in unknown))
                )

        await self.session.execute(
            sa.delete(AgentModelAccess).where(
                AgentModelAccess.organization_id == self.organization_id,
                AgentModelAccess.workspace_id == workspace_id,
                AgentModelAccess.catalog_id.not_in(sorted(desired_catalog_ids)),
            )
        )
        if desired_catalog_ids:
            await self.session.execute(
                pg_insert(AgentModelAccess)
                .values(
                    [
                        {
                            "id": uuid4(),
                            "organization_id": self.organization_id,
                            "workspace_id": workspace_id,
                            "catalog_id": catalog_id,
                        }
                        for catalog_id in sorted(desired_catalog_ids)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "workspace_id", "catalog_id"]
                )
            )
        await self.session.commit()
        return await self._read_workspace_model_access(workspace_id)

    async def _read_workspace_model_access(
        self, workspace_id: UUID
    ) -> WorkspaceAgentModelAccessRead:
        catalog_ids = list(
            await self.session.scalars(
                select(AgentModelAccess.catalog_id)
                .where(
                    AgentModelAccess.organization_id == self.organization_id,
                    AgentModelAccess.workspace_id == workspace_id,
                )
                .order_by(AgentModelAccess.catalog_id)
            )
        )
        models = await self._list_labelled_models(
            effective_catalog_ids(self.organization_id, None)
        )
        candidates = [
            WorkspaceAgentModelCandidate.model_validate(
                model.model_dump(exclude={"custom_provider_base_url"})
            )
            for model in models
        ]
        return WorkspaceAgentModelAccessRead(
            inherit_all=not catalog_ids,
            catalog_ids=catalog_ids,
            candidates=candidates,
        )

    async def _list_labelled_models(
        self, catalog_ids: Select[tuple[UUID]]
    ) -> list[WorkspaceAgentModelRead]:
        """Load org-visible catalog rows with their custom provider labels."""
        stmt = (
            select(
                AgentCatalog,
                AgentCustomProvider.display_name,
                AgentCustomProvider.base_url,
            )
            .outerjoin(
                AgentCustomProvider,
                sa.and_(
                    AgentCustomProvider.id == AgentCatalog.custom_provider_id,
                    AgentCustomProvider.organization_id == self.organization_id,
                ),
            )
            .where(
                AgentCatalog.id.in_(catalog_ids),
                sa.or_(
                    AgentCatalog.organization_id == self.organization_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
            )
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return [
            WorkspaceAgentModelRead(
                **AgentCatalogRead.model_validate(row).model_dump(),
                custom_provider_name=provider_name,
                custom_provider_base_url=provider_base_url,
            )
            for row, provider_name, provider_base_url in rows
        ]

    async def is_catalog_enabled(
        self,
        catalog_id: UUID,
        workspace_id: UUID | None = None,
    ) -> bool:
        """Return whether a catalog row is enabled under effective access rules."""
        enabled = await self.session.scalar(
            select(
                exists().where(
                    AgentCatalog.id == catalog_id,
                    AgentCatalog.id.in_(
                        effective_catalog_ids(self.organization_id, workspace_id)
                    ),
                    sa.or_(
                        AgentCatalog.organization_id.is_(None),
                        AgentCatalog.organization_id == self.organization_id,
                    ),
                )
            )
        )
        return bool(enabled)
