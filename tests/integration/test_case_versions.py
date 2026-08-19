"""End-to-end persistence tests for case text-field versions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseVersionField,
)
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.cases.versions.service import CaseVersionsService
from tracecat.db.models import Case, CaseVersion, User, Workspace

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]


@pytest.fixture(autouse=True)
def isolate_case_side_effects() -> Iterator[None]:
    """Keep the integration focused on database state and case activity."""
    with (
        patch.object(
            CasesService,
            "has_entitlement",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            return_value=None,
        ),
        patch(
            "tracecat.cases.events.publish_case_event_payload",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.cases.service.publish_case_event_payload",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


async def test_case_version_lifecycle(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Create, update, no-op, attribute, and delete versions as one workflow."""
    assert svc_role.user_id is not None
    session.add(
        User(
            id=svc_role.user_id,
            email=f"case-version-{svc_role.user_id}@example.com",
            hashed_password="hashed",
        )
    )
    await session.flush()

    service = CasesService(session=session, role=svc_role)
    case = await service.create_case(
        CaseCreate(
            summary="Initial summary",
            description="<p>Initial description</p>",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    await service.update_case(
        case,
        CaseUpdate(
            summary="Updated summary",
            description="<p>Updated description</p>",
        ),
    )
    await service.update_case(
        case,
        CaseUpdate(
            summary="Updated summary",
            description="<p>Updated description</p>",
        ),
    )
    await service.update_case(case, CaseUpdate(summary="Updated summary "))

    versions = (
        (
            await session.execute(
                select(CaseVersion)
                .where(CaseVersion.case_id == case.id)
                .order_by(CaseVersion.field, CaseVersion.version)
            )
        )
        .scalars()
        .all()
    )
    assert [
        (version.field, version.version, version.content) for version in versions
    ] == [
        (CaseVersionField.SUMMARY, 1, "Initial summary"),
        (CaseVersionField.SUMMARY, 2, "Updated summary"),
        (CaseVersionField.SUMMARY, 3, "Updated summary "),
        (CaseVersionField.DESCRIPTION, 1, "<p>Initial description</p>"),
        (CaseVersionField.DESCRIPTION, 2, "<p>Updated description</p>"),
    ]
    assert {version.user_id for version in versions} == {svc_role.user_id}

    description_events = [
        event
        for event in await service.events.list_events(case)
        if event.type == CaseEventType.CASE_UPDATED
        and event.data["field"] == "description"
    ]
    assert len(description_events) == 1
    assert description_events[0].data["old"] == "<p>Initial description</p>"
    assert description_events[0].data["new"] == "<p>Updated description</p>"

    await service.delete_case(case)
    assert (
        not (
            await session.execute(
                select(CaseVersion.id).where(CaseVersion.case_id == case.id)
            )
        )
        .scalars()
        .all()
    )


async def test_concurrent_case_version_allocation(svc_role: Role) -> None:
    """Two writers serialize into unique, consecutive field versions."""
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
    assert role.workspace_id is not None
    assert role.organization_id is not None
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    case_id = uuid.uuid4()

    try:
        async with session_factory() as seed_session:
            seed_session.add_all(
                [
                    Workspace(
                        id=role.workspace_id,
                        name="case-version-concurrency-workspace",
                        organization_id=role.organization_id,
                    ),
                    Case(
                        id=case_id,
                        workspace_id=role.workspace_id,
                        case_number=1,
                        summary="Initial summary",
                        description="Initial description",
                        status=CaseStatus.NEW,
                        priority=CasePriority.MEDIUM,
                        severity=CaseSeverity.LOW,
                    ),
                    CaseVersion(
                        workspace_id=role.workspace_id,
                        case_id=case_id,
                        field=CaseVersionField.SUMMARY,
                        version=1,
                        content="Initial summary",
                    ),
                ]
            )
            await seed_session.commit()

        async def append(content: str) -> int:
            async with session_factory() as concurrent_session:
                version = await CaseVersionsService(
                    session=concurrent_session,
                    role=role.model_copy(deep=True),
                ).append_version(
                    case_id=case_id,
                    field=CaseVersionField.SUMMARY,
                    content=content,
                )
                await concurrent_session.commit()
                return version.version

        assert sorted(await asyncio.gather(append("Second"), append("Third"))) == [
            2,
            3,
        ]

        async with session_factory() as verification_session:
            versions = (
                await verification_session.execute(
                    select(CaseVersion.version)
                    .where(CaseVersion.case_id == case_id)
                    .order_by(CaseVersion.version)
                )
            ).scalars()
            assert versions.all() == [1, 2, 3]
    finally:
        await engine.dispose()
