"""Artifact mentions resolve per viewer, and degrade to a label otherwise."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_organization_with_workspace
from tracecat import config
from tracecat.agent.references.uri import (
    ReferenceKind,
    ReferenceTarget,
    parse_reference_uri,
)
from tracecat.artifacts.mentions import (
    ArtifactCardField,
    ArtifactFieldColor,
    ArtifactFieldKind,
    ArtifactResolver,
    ResolvedArtifact,
)
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import User

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


def _ref(artifact_type: str, artifact_id: uuid.UUID) -> ReferenceTarget:
    return parse_reference_uri(
        f"tracecat-ref://v1/artifact/{artifact_type}/{artifact_id}"
    )


async def test_case_resolves_to_channel_safe_card_and_url(
    session: AsyncSession, svc_role: Role
) -> None:
    case = await CasesService(session, svc_role).create_case(
        CaseCreate(
            summary="Beacon on build host",
            description="Payload with IOCs that must not reach a channel.",
            status=CaseStatus.IN_PROGRESS,
            priority=CasePriority.HIGH,
            severity=CaseSeverity.CRITICAL,
        )
    )

    result = await ArtifactResolver(session).resolve(svc_role, _ref("case", case.id))

    assert result == ResolvedArtifact(
        title="Beacon on build host",
        display_id=case.short_id,
        fields=(
            ArtifactCardField(
                "severity",
                "Severity",
                ArtifactFieldKind.SEVERITY,
                "Critical",
                ArtifactFieldColor.RED,
            ),
            ArtifactCardField(
                "priority",
                "Priority",
                ArtifactFieldKind.PRIORITY,
                "High",
                ArtifactFieldColor.RED,
            ),
            ArtifactCardField("tags", "Tags", ArtifactFieldKind.TAGS, ()),
            ArtifactCardField(
                "status",
                "Status",
                ArtifactFieldKind.STATUS,
                "In progress",
                ArtifactFieldColor.YELLOW,
            ),
            ArtifactCardField(
                "assignee", "Assignee", ArtifactFieldKind.USER_TEXT, "Unassigned"
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
        ),
        url=f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{svc_role.workspace_id}/cases/{case.id}",
    )


async def test_detail_adds_description_assignee_and_tags(
    session: AsyncSession, svc_role: Role
) -> None:
    assignee = User(
        email="analyst@example.com",
        first_name="Ada",
        last_name="Analyst",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
    )
    session.add(assignee)
    await session.commit()
    case = await CasesService(session, svc_role).create_case(
        CaseCreate(
            summary="Beacon on build host",
            description="Seen on two hosts.",
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.MEDIUM,
            assignee_id=assignee.id,
        )
    )
    ref = _ref("case", case.id)
    tags = CaseTagsService(session, svc_role)
    for name in ("edr", "after-hours"):
        await tags.add_case_tag(case.id, name, create_if_missing=True)
    session.expire_all()
    resolver = ArtifactResolver(session)

    card = await resolver.resolve(svc_role, ref)
    pane = await resolver.resolve(svc_role, ref, detail=True)

    assert card is not None and pane is not None
    card_fields = {f.key: f for f in card.fields}
    pane_fields = {f.key: f for f in pane.fields}
    assert "description" not in card_fields
    assert pane_fields["description"].value == "Seen on two hosts."
    assert card_fields["assignee"].value == "Ada Analyst"
    tags = card_fields["tags"].value
    assert isinstance(tags, tuple) and sorted(tags) == ["after-hours", "edr"]
    assert card_fields["status"].color is ArtifactFieldColor.BLUE
    assert card_fields["severity"].color is ArtifactFieldColor.YELLOW
    assert card_fields["priority"].color is ArtifactFieldColor.GRAY


async def test_case_is_hidden_without_case_read_or_from_another_workspace(
    session: AsyncSession, svc_role: Role, intake_role: Role
) -> None:
    case = await CasesService(session, svc_role).create_case(
        CaseCreate(
            summary="Phishing report",
            description="",
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
    )
    other = await create_organization_with_workspace(session, name="other")
    outsider = svc_role.model_copy(
        update={"workspace_id": other.id, "organization_id": other.organization_id}
    )
    resolver = ArtifactResolver(session)

    forbidden = await resolver.resolve(intake_role, _ref("case", case.id))
    elsewhere = await resolver.resolve(outsider, _ref("case", case.id))
    missing = await resolver.resolve(svc_role, _ref("case", uuid.uuid4()))

    assert forbidden is elsewhere is missing is None


async def test_form_mentions_do_not_parse() -> None:
    # Forms are not mentionable: a form reference renders as its label only.
    with pytest.raises(ValueError):
        _ref("form", uuid.uuid4())


async def test_other_reference_kinds_do_not_resolve(
    session: AsyncSession, svc_role: Role
) -> None:
    table = ReferenceTarget(ReferenceKind.TABLE, str(uuid.uuid4()))

    assert await ArtifactResolver(session).resolve(svc_role, table) is None
