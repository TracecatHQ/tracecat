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
    ArtifactResolver,
    Forbidden,
    ResolvedArtifact,
    Unresolved,
)
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService

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
        artifact=CaseArtifact(
            id=str(case.id),
            title="Beacon on build host",
            severity=CaseSeverity.CRITICAL,
            status=CaseStatus.IN_PROGRESS,
        ),
        card=(
            ArtifactCardField("Case", case.short_id),
            ArtifactCardField("Status", "In progress"),
            ArtifactCardField("Severity", "Critical"),
            ArtifactCardField("Priority", "High"),
        ),
        url=f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{svc_role.workspace_id}/cases/{case.id}",
    )


async def test_case_is_forbidden_without_case_read_and_unresolved_elsewhere(
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

    assert forbidden == Forbidden()
    assert elsewhere == missing == Unresolved()


async def test_form_mentions_do_not_parse() -> None:
    # Forms are not mentionable: a form reference renders as its label only.
    with pytest.raises(ValueError):
        _ref("form", uuid.uuid4())


async def test_other_reference_kinds_are_unresolved(
    session: AsyncSession, svc_role: Role
) -> None:
    table = ReferenceTarget(ReferenceKind.TABLE, str(uuid.uuid4()))

    assert await ArtifactResolver(session).resolve(svc_role, table) == Unresolved()
