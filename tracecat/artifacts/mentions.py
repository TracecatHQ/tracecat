"""Resolve artifact mentions in agent replies for the viewer who reads them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.references.uri import ArtifactMentionType, ReferenceTarget
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

    title: str
    fields: tuple[ArtifactCardField, ...]
    url: str


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

    async def resolve(
        self, role: Role, ref: ReferenceTarget
    ) -> ResolvedArtifact | None:
        """None when the viewer may not see it, or it is missing or not a case."""
        if ref.artifact_type is not ArtifactMentionType.CASE:
            return None
        if role.workspace_id is None or not has_scope(
            role.scopes or frozenset(), "case:read"
        ):
            return None
        case_id = uuid.UUID(ref.identity)
        service = CasesService(self.session, role)
        case = await service.get_case(case_id)
        if case is None:
            return None
        return ResolvedArtifact(
            title=case.summary,
            fields=(
                ArtifactCardField("Case", case.short_id),
                ArtifactCardField("Status", _label(case.status)),
                ArtifactCardField("Severity", _label(case.severity)),
                ArtifactCardField("Priority", _label(case.priority)),
            ),
            url=_case_url(service.workspace_id, case_id),
        )
