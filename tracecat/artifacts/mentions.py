"""Resolve artifact mentions in agent replies for the viewer who reads them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.references.uri import (
    ArtifactMentionType,
    ReferenceDiagnosticCode,
    ReferenceKind,
    ReferenceTarget,
)
from tracecat.artifacts.schemas import Artifact, CaseArtifact
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.cases.service import CasesService


@dataclass(frozen=True, slots=True)
class ArtifactCardField:
    """A label and value safe to show everyone in a shared channel."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    """A mentioned artifact the viewer may see, ready for any place to draw."""

    artifact: Artifact
    card: tuple[ArtifactCardField, ...]
    url: str


@dataclass(frozen=True, slots=True)
class Unresolved:
    """Unknown type, missing, or hidden from the viewer: show the label only."""

    code: ClassVar[ReferenceDiagnosticCode] = ReferenceDiagnosticCode.UNRESOLVED


@dataclass(frozen=True, slots=True)
class Forbidden:
    """The viewer lacks the scope to read this type: show the label only."""

    code: ClassVar[ReferenceDiagnosticCode] = ReferenceDiagnosticCode.FORBIDDEN


type ArtifactResolution = ResolvedArtifact | Unresolved | Forbidden


def _label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _case_url(workspace_id: uuid.UUID, case_id: uuid.UUID) -> str:
    return (
        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{workspace_id}/cases/{case_id}"
    )


class ArtifactResolver:
    """Turn an artifact mention into what one viewer may see of it.

    Authorizes mentions for renderers outside the web app. They get a
    label-only result for anything else and must never show an error for it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, role: Role, ref: ReferenceTarget) -> ArtifactResolution:
        if ref.kind != ReferenceKind.ARTIFACT:
            return Unresolved()
        if role.workspace_id is None:
            return Forbidden()
        artifact_id = uuid.UUID(ref.identity)
        match ref.artifact_type:
            case ArtifactMentionType.CASE:
                return await self._resolve_case(role, artifact_id)
            case None:
                return Unresolved()

    async def _resolve_case(self, role: Role, case_id: uuid.UUID) -> ArtifactResolution:
        if not has_scope(role.scopes or frozenset(), "case:read"):
            return Forbidden()
        service = CasesService(self.session, role)
        case = await service.get_case(case_id)
        if case is None:
            return Unresolved()
        return ResolvedArtifact(
            artifact=CaseArtifact(
                id=str(case_id),
                title=case.summary,
                severity=case.severity,
                status=case.status,
            ),
            card=(
                ArtifactCardField("Case", case.short_id),
                ArtifactCardField("Status", _label(case.status)),
                ArtifactCardField("Severity", _label(case.severity)),
                ArtifactCardField("Priority", _label(case.priority)),
            ),
            url=_case_url(service.workspace_id, case_id),
        )
