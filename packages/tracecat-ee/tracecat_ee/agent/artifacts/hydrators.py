"""Enterprise artifact hydrators for Workspace Chat working sets."""

from __future__ import annotations

import uuid

from tracecat.agent.artifacts.hydration import (
    ArtifactHydrationContext,
    ArtifactHydratorRegistry,
    MountedArtifactContent,
)
from tracecat.artifacts.schemas import Artifact, CaseArtifact
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


def build_hydrator_registry() -> ArtifactHydratorRegistry:
    """Build the EE source-available artifact hydrator registry."""
    return ArtifactHydratorRegistry({"case": CaseArtifactHydrator()})


def _require_case_read_scope(ctx: ArtifactHydrationContext) -> None:
    scopes = ctx.role.scopes or frozenset()
    if has_scope(scopes, "case:read"):
        return
    raise ScopeDeniedError(
        required_scopes=["case:read"],
        missing_scopes=["case:read"],
    )
