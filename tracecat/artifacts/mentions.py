"""Resolve an artifact mention to what a role may see of it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.references.uri import ArtifactMentionType, ReferenceTarget
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.service import CasesService
from tracecat.db.models import Case, User


class ArtifactFieldKind(StrEnum):
    """What a field's value means, so each place can draw it natively."""

    STATUS = "status"
    SEVERITY = "severity"
    PRIORITY = "priority"
    TEXT = "text"
    # Seconds since the epoch.
    TIMESTAMP = "timestamp"
    TAGS = "tags"
    # A person's name as plain text, never a provider user ID.
    USER_TEXT = "user_text"


class ArtifactFieldColor(StrEnum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    GRAY = "gray"


type ArtifactFieldValue = str | int | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtifactCardField:
    """One field of a resolved artifact, in display order."""

    key: str
    label: str
    kind: ArtifactFieldKind
    value: ArtifactFieldValue
    color: ArtifactFieldColor | None = None


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    """A mentioned artifact the viewer may see, ready for any place to draw."""

    title: str
    display_id: str
    fields: tuple[ArtifactCardField, ...]
    url: str


_SEVERITY_COLORS = {
    CaseSeverity.CRITICAL: ArtifactFieldColor.RED,
    CaseSeverity.FATAL: ArtifactFieldColor.RED,
    CaseSeverity.HIGH: ArtifactFieldColor.RED,
    CaseSeverity.MEDIUM: ArtifactFieldColor.YELLOW,
}
_PRIORITY_COLORS = {
    CasePriority.CRITICAL: ArtifactFieldColor.RED,
    CasePriority.HIGH: ArtifactFieldColor.RED,
    CasePriority.MEDIUM: ArtifactFieldColor.YELLOW,
}
_STATUS_COLORS = {
    CaseStatus.NEW: ArtifactFieldColor.BLUE,
    CaseStatus.IN_PROGRESS: ArtifactFieldColor.YELLOW,
    CaseStatus.RESOLVED: ArtifactFieldColor.GREEN,
    CaseStatus.CLOSED: ArtifactFieldColor.GREEN,
}


def _label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _case_url(workspace_id: uuid.UUID, case_id: uuid.UUID) -> str:
    return (
        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{workspace_id}/cases/{case_id}"
    )


def _person(user: User | None) -> str:
    if user is None:
        return "Unassigned"
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full_name or user.email


def _case_fields(case: Case, *, detail: bool) -> tuple[ArtifactCardField, ...]:
    fields = [
        ArtifactCardField(
            "severity",
            "Severity",
            ArtifactFieldKind.SEVERITY,
            _label(case.severity),
            _SEVERITY_COLORS.get(case.severity, ArtifactFieldColor.GRAY),
        ),
        ArtifactCardField(
            "priority",
            "Priority",
            ArtifactFieldKind.PRIORITY,
            _label(case.priority),
            _PRIORITY_COLORS.get(case.priority, ArtifactFieldColor.GRAY),
        ),
        # Same order the case API returns them in.
        ArtifactCardField(
            "tags", "Tags", ArtifactFieldKind.TAGS, tuple(t.name for t in case.tags)
        ),
        ArtifactCardField(
            "status",
            "Status",
            ArtifactFieldKind.STATUS,
            _label(case.status),
            _STATUS_COLORS.get(case.status, ArtifactFieldColor.GRAY),
        ),
        ArtifactCardField(
            "assignee", "Assignee", ArtifactFieldKind.USER_TEXT, _person(case.assignee)
        ),
        ArtifactCardField(
            "updated_at",
            "Updated",
            ArtifactFieldKind.TIMESTAMP,
            int(case.updated_at.timestamp()),
        ),
        ArtifactCardField(
            "created_at",
            "Created",
            ArtifactFieldKind.TIMESTAMP,
            int(case.created_at.timestamp()),
        ),
    ]
    if detail and case.description:
        fields.append(
            ArtifactCardField(
                "description", "Description", ArtifactFieldKind.TEXT, case.description
            )
        )
    return tuple(fields)


class ArtifactResolver:
    """Turn an artifact mention into what one role may see of it.

    For renderers outside the web app. None for anything the role may not see:
    callers fall back to the label and never show an error.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self, role: Role, ref: ReferenceTarget, *, detail: bool = False
    ) -> ResolvedArtifact | None:
        """None when the viewer may not see it, or it is missing or not a case.

        `detail` adds what only a details view shows the viewer (the description);
        never pass it for anything posted where others can read it.
        """
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
            display_id=case.short_id,
            fields=_case_fields(case, detail=detail),
            url=_case_url(service.workspace_id, case_id),
        )
