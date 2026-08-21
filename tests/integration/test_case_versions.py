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
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseVersionField,
)
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
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


@pytest.mark.parametrize(
    "use_batch_update",
    [False, True],
    ids=["update_case", "batch_update_cases"],
)
async def test_concurrent_case_version_allocation(
    svc_role: Role,
    use_batch_update: bool,
) -> None:
    """Concurrent case write paths allocate unique, consecutive versions."""
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

        async def update(content: str) -> None:
            async with session_factory() as concurrent_session:
                service = CasesService(
                    session=concurrent_session,
                    role=role.model_copy(deep=True),
                )
                params = CaseUpdate(summary=content)
                if use_batch_update:
                    response = await service.batch_update_cases([case_id], params)
                    assert response.succeeded == 1
                else:
                    case = await concurrent_session.scalar(
                        select(Case).where(Case.id == case_id)
                    )
                    assert case is not None
                    await service.update_case(case, params)

        await asyncio.gather(update("Second"), update("Third"))

        async with session_factory() as verification_session:
            versions = (
                await verification_session.execute(
                    select(CaseVersion.version, CaseVersion.content)
                    .where(CaseVersion.case_id == case_id)
                    .order_by(CaseVersion.version)
                )
            ).tuples()
            version_rows = versions.all()
            assert [version for version, _ in version_rows] == [1, 2, 3]
            assert version_rows[0] == (1, "Initial summary")
            assert {content for _, content in version_rows[1:]} == {
                "Second",
                "Third",
            }
    finally:
        await engine.dispose()
