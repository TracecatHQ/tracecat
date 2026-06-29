"""Resolve artifact identity refs into canonical artifacts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.artifacts.bindings import ArtifactIdentityRef, ArtifactSideEffect
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus
from tracecat.cases.service import CaseCommentsService, CasesService, CaseTasksService
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.tables.service import TablesService


@dataclass(frozen=True, slots=True)
class _ResolvedArtifactIdentity:
    id: str
    title: str
    severity: CaseSeverity | None = None
    status: CaseStatus | None = None


async def resolve_artifact_side_effects(
    effects: Sequence[ArtifactSideEffect],
    *,
    session: AsyncSession,
    role: Role,
) -> list[ArtifactSideEffect]:
    """Resolve domain identity refs before artifact persistence or streaming."""
    if not effects:
        return []

    case_service: CasesService | None = None
    case_comments_service: CaseCommentsService | None = None
    case_tasks_service: CaseTasksService | None = None
    table_service: TablesService | None = None
    resolved_effects: list[ArtifactSideEffect] = []
    for effect in effects:
        identity_ref = effect.identity_ref
        if identity_ref is None:
            resolved_effects.append(effect)
            continue

        resolved_identity: _ResolvedArtifactIdentity | None = None
        match identity_ref.artifact_type:
            case "case":
                case_service = case_service or CasesService(session, role=role)
                if identity_ref.ref_kind == "comment_id":
                    case_comments_service = (
                        case_comments_service
                        or CaseCommentsService(
                            session,
                            role=role,
                        )
                    )
                if identity_ref.ref_kind == "task_id":
                    case_tasks_service = case_tasks_service or CaseTasksService(
                        session,
                        role=role,
                    )
                resolved_identity = await _resolve_case_identity_ref(
                    case_service,
                    identity_ref,
                    comment_service=case_comments_service,
                    task_service=case_tasks_service,
                )
            case "table":
                table_service = table_service or TablesService(session, role=role)
                resolved_identity = await _resolve_table_identity_ref(
                    table_service,
                    identity_ref,
                )
            case _:
                resolved_identity = None

        if resolved_identity is None:
            logger.warning(
                "Failed to resolve artifact identity ref",
                artifact_type=identity_ref.artifact_type,
                ref_kind=identity_ref.ref_kind,
            )
            continue

        update: dict[str, object] = {
            "id": resolved_identity.id,
            "title": resolved_identity.title,
        }
        if effect.artifact.type == "case":
            if resolved_identity.severity is not None:
                update["severity"] = resolved_identity.severity
            if resolved_identity.status is not None:
                update["status"] = resolved_identity.status

        resolved_effects.append(
            ArtifactSideEffect(
                op=effect.op,
                artifact=effect.artifact.model_copy(update=update),
            )
        )
    return resolved_effects


async def _resolve_case_identity_ref(
    case_service: CasesService,
    identity_ref: ArtifactIdentityRef,
    *,
    comment_service: CaseCommentsService | None = None,
    task_service: CaseTasksService | None = None,
) -> _ResolvedArtifactIdentity | None:
    try:
        match identity_ref.ref_kind:
            case "id":
                case_id = uuid.UUID(identity_ref.ref)
            case "comment_id":
                if comment_service is None:
                    return None
                comment = await comment_service.get_comment(uuid.UUID(identity_ref.ref))
                if comment is None:
                    return None
                case_id = comment.case_id
            case "task_id":
                if task_service is None:
                    return None
                task = await task_service.get_task(uuid.UUID(identity_ref.ref))
                case_id = task.case_id
            case _:
                return None
    except (TracecatNotFoundError, ValueError):
        return None

    case = await case_service.get_case(case_id)
    if case is None:
        return None
    return _ResolvedArtifactIdentity(
        id=str(case.id),
        title=case.summary,
        severity=case.severity,
        status=case.status,
    )


async def _resolve_table_identity_ref(
    table_service: TablesService,
    identity_ref: ArtifactIdentityRef,
) -> _ResolvedArtifactIdentity | None:
    match identity_ref.ref_kind:
        case "name":
            try:
                table = await table_service.get_table_by_name(identity_ref.ref)
            except (TracecatNotFoundError, ValueError):
                return None
        case "id":
            try:
                table_id = uuid.UUID(identity_ref.ref)
            except ValueError:
                return None
            try:
                table = await table_service.get_table(table_id)
            except TracecatNotFoundError:
                return None
        case _:
            return None

    return _ResolvedArtifactIdentity(id=str(table.id), title=table.name)
