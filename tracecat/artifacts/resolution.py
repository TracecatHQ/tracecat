"""Resolve artifact identity refs into canonical artifacts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.artifacts.bindings import ArtifactIdentityRef, ArtifactSideEffect
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.tables.service import TablesService


@dataclass(frozen=True, slots=True)
class _ResolvedArtifactIdentity:
    id: str
    title: str


async def resolve_artifact_side_effects(
    effects: Sequence[ArtifactSideEffect],
    *,
    session: AsyncSession,
    role: Role,
) -> list[ArtifactSideEffect]:
    """Resolve domain identity refs before artifact persistence or streaming."""
    if not effects:
        return []

    table_service: TablesService | None = None
    resolved_effects: list[ArtifactSideEffect] = []
    for effect in effects:
        identity_ref = effect.identity_ref
        if identity_ref is None:
            resolved_effects.append(effect)
            continue

        resolved_identity: _ResolvedArtifactIdentity | None = None
        match identity_ref.artifact_type:
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

        resolved_effects.append(
            ArtifactSideEffect(
                op=effect.op,
                artifact=effect.artifact.model_copy(
                    update={
                        "id": resolved_identity.id,
                        "title": resolved_identity.title,
                    }
                ),
            )
        )
    return resolved_effects


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
