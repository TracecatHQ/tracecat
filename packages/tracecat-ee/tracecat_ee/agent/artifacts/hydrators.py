"""Enterprise artifact hydrators for Workspace Chat working sets."""

from __future__ import annotations

import uuid

from pydantic_core import to_jsonable_python

from tracecat.agent.artifacts.hydration import (
    ArtifactHydrationContext,
    ArtifactHydratorRegistry,
    MountedArtifactContent,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.artifacts.schemas import (
    AgentArtifact,
    Artifact,
    CaseArtifact,
    TableArtifact,
)
from tracecat.auth.schemas import UserRead
from tracecat.authz.controls import has_scope
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.dropdowns.service import CaseDropdownValuesService
from tracecat.cases.rows.service import CaseTableRowsService
from tracecat.cases.schemas import (
    CaseFieldRead,
    CaseFieldReadMinimal,
    CaseRead,
)
from tracecat.cases.service import CasesService
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.db.engine import get_async_session_context_manager
from tracecat.exceptions import ScopeDeniedError
from tracecat.logger import logger
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnRead, TableRead
from tracecat.tables.service import TablesService
from tracecat.tiers.enums import Entitlement


class CaseArtifactHydrator:
    """Hydrate a case artifact projection into a full case read model."""

    async def hydrate(
        self,
        artifact: Artifact,
        ctx: ArtifactHydrationContext,
    ) -> MountedArtifactContent | None:
        """Load the current case content for the mounted working copy."""
        if not isinstance(artifact, CaseArtifact):
            return None

        try:
            case_id = uuid.UUID(artifact.id)
        except ValueError:
            logger.warning(
                "Cannot hydrate case artifact with non-UUID id",
                artifact_id=artifact.id,
            )
            return None

        _require_case_read_scope(ctx)

        async with get_async_session_context_manager() as session:
            service = CasesService(session, ctx.role)
            case = await service.get_case(case_id, track_view=False)
            if case is None:
                logger.warning("Cannot hydrate missing case artifact", case_id=case_id)
                return None

            fields = await service.fields.get_fields(case) or {}
            field_definitions = await service.fields.list_fields()
            field_schema = await service.fields.get_field_schema()
            final_fields: list[CaseFieldRead] = []
            for definition in field_definitions:
                field = CaseFieldReadMinimal.from_sa(
                    definition,
                    field_schema=field_schema,
                )
                final_fields.append(
                    CaseFieldRead(
                        **field.model_dump(),
                        value=fields.get(field.id),
                    )
                )

            dropdown_service = CaseDropdownValuesService(session, ctx.role)
            dropdown_values: list[CaseDropdownValueRead] = []
            if await dropdown_service.has_entitlement(Entitlement.CASE_ADDONS):
                dropdown_values = await dropdown_service.list_values_for_case(case.id)

            rows_by_case = await CaseTableRowsService(
                session,
                ctx.role,
            ).hydrate_case_rows(
                case_ids=[case.id],
                include_row_data=True,
            )
            hydrated = CaseRead(
                id=case.id,
                short_id=case.short_id,
                created_at=case.created_at,
                updated_at=case.updated_at,
                summary=case.summary,
                status=case.status,
                priority=case.priority,
                severity=case.severity,
                description=case.description,
                assignee=UserRead.model_validate(case.assignee, from_attributes=True)
                if case.assignee
                else None,
                fields=final_fields,
                payload=case.payload,
                tags=[
                    CaseTagRead.model_validate(tag, from_attributes=True)
                    for tag in case.tags
                ],
                dropdown_values=dropdown_values,
                rows=rows_by_case.get(case.id, []),
            )

        return MountedArtifactContent(
            filename="case.json",
            content_type="case.read",
            payload=hydrated.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
        )


class TableArtifactHydrator:
    """Hydrate a table artifact projection into metadata and a row preview."""

    async def hydrate(
        self,
        artifact: Artifact,
        ctx: ArtifactHydrationContext,
    ) -> MountedArtifactContent | None:
        """Load the current table schema and first page of rows."""
        if not isinstance(artifact, TableArtifact):
            return None

        try:
            table_id = uuid.UUID(artifact.id)
        except ValueError:
            logger.warning(
                "Cannot hydrate table artifact with non-UUID id",
                artifact_id=artifact.id,
            )
            return None

        _require_table_read_scope(ctx)

        async with get_async_session_context_manager() as session:
            service = TablesService(session, ctx.role)
            table = await service.get_table(table_id)
            index_columns = await service.get_index(table)
            table_read = TableRead(
                id=table.id,
                name=table.name,
                columns=[
                    TableColumnRead(
                        id=column.id,
                        name=column.name,
                        type=SqlType(column.type),
                        nullable=column.nullable,
                        default=column.default,
                        is_index=column.name in index_columns,
                        options=column.options,
                    )
                    for column in table.columns
                ],
            )
            rows_page = await service.list_rows(
                table,
                CursorPaginationParams(limit=100),
            )

        return MountedArtifactContent(
            filename="table.json",
            content_type="table.read",
            payload={
                "table": table_read.model_dump(mode="json", by_alias=True),
                "rows": to_jsonable_python(rows_page.items, fallback=str),
                "pagination": {
                    "next_cursor": rows_page.next_cursor,
                    "prev_cursor": rows_page.prev_cursor,
                    "has_more": rows_page.has_more,
                    "has_previous": rows_page.has_previous,
                    "total_estimate": rows_page.total_estimate,
                },
            },
        )


class AgentArtifactHydrator:
    """Hydrate an agent artifact projection into a full preset read model."""

    async def hydrate(
        self,
        artifact: Artifact,
        ctx: ArtifactHydrationContext,
    ) -> MountedArtifactContent | None:
        """Load the current agent preset content for the mounted working copy."""
        if not isinstance(artifact, AgentArtifact):
            return None

        try:
            preset_id = uuid.UUID(artifact.id)
        except ValueError:
            logger.warning(
                "Cannot hydrate agent artifact with non-UUID id",
                artifact_id=artifact.id,
            )
            return None

        _require_agent_read_scope(ctx)

        async with AgentPresetService.with_session(role=ctx.role) as service:
            preset = await service.get_preset(preset_id)
            if preset is None:
                logger.warning(
                    "Cannot hydrate missing agent artifact",
                    preset_id=preset_id,
                )
                return None
            preset_read = await service.build_preset_read(preset)

        return MountedArtifactContent(
            filename="agent.json",
            content_type="agent_preset.read",
            payload=preset_read.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
        )


def build_hydrator_registry() -> ArtifactHydratorRegistry:
    """Build the EE source-available artifact hydrator registry."""
    return ArtifactHydratorRegistry(
        {
            "case": CaseArtifactHydrator(),
            "table": TableArtifactHydrator(),
            "agent": AgentArtifactHydrator(),
        }
    )


def _require_case_read_scope(ctx: ArtifactHydrationContext) -> None:
    _require_artifact_scope(ctx, "case:read")


def _require_table_read_scope(ctx: ArtifactHydrationContext) -> None:
    _require_artifact_scope(ctx, "table:read")


def _require_agent_read_scope(ctx: ArtifactHydrationContext) -> None:
    _require_artifact_scope(ctx, "agent:read")


def _require_artifact_scope(ctx: ArtifactHydrationContext, required_scope: str) -> None:
    scopes = ctx.role.scopes or frozenset()
    if has_scope(scopes, required_scope):
        return
    raise ScopeDeniedError(
        required_scopes=[required_scope],
        missing_scopes=[required_scope],
    )
