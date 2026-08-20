"""End-to-end persistence tests for case text-field versions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, update
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
from tracecat.db.models import Case, CaseEvent, CaseVersion, User, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import PageParams

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


async def test_case_version_history_pagination_filter_and_compare(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """History stays stable across tied timestamps and exposes predecessor content."""
    assert svc_role.user_id is not None
    actor_email = f"case-history-{svc_role.user_id}@example.com"
    session.add(
        User(
            id=svc_role.user_id,
            email=actor_email,
            hashed_password="hashed",
        )
    )
    await session.flush()

    service = CasesService(session=session, role=svc_role)
    case = await service.create_case(
        CaseCreate(
            summary="Summary v1",
            description="Description v1",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    await service.update_case(case, CaseUpdate(summary="Summary v2"))
    await service.update_case(case, CaseUpdate(description="Description v2"))

    tied_timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await session.execute(
        update(CaseVersion)
        .where(CaseVersion.case_id == case.id)
        .values(created_at=tied_timestamp)
    )
    await session.commit()

    expected_ids = list(
        (
            await session.execute(
                select(CaseVersion.id)
                .where(CaseVersion.case_id == case.id)
                .order_by(
                    CaseVersion.created_at.desc(),
                    CaseVersion.surrogate_id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    history_ids: list[uuid.UUID] = []
    history = []
    cursor: str | None = None
    while True:
        page = await service.versions.list_versions(
            case_id=case.id,
            page=PageParams(limit=1, cursor=cursor),
        )
        history.extend(page.items)
        history_ids.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert history_ids == expected_ids
    assert len(history_ids) == len(set(history_ids)) == 4
    assert {item.field for item in history if item.is_latest} == {
        CaseVersionField.SUMMARY,
        CaseVersionField.DESCRIPTION,
    }
    assert {item.actor.email for item in history if item.actor is not None} == {
        actor_email
    }
    assert all("content" not in item.model_dump() for item in history)

    summary_history = await service.versions.list_versions(
        case_id=case.id,
        page=PageParams(limit=10),
        field=CaseVersionField.SUMMARY,
    )
    assert [item.version for item in summary_history.items] == [2, 1]
    assert [item.is_latest for item in summary_history.items] == [True, False]

    summary_versions = (
        (
            await session.execute(
                select(CaseVersion)
                .where(
                    CaseVersion.case_id == case.id,
                    CaseVersion.field == CaseVersionField.SUMMARY,
                )
                .order_by(CaseVersion.version)
            )
        )
        .scalars()
        .all()
    )
    baseline = await service.versions.compare_with_predecessor(
        case_id=case.id,
        version_id=summary_versions[0].id,
    )
    assert baseline is not None
    assert baseline.selected.content == "Summary v1"
    assert baseline.predecessor is None

    comparison = await service.versions.compare_with_predecessor(
        case_id=case.id,
        version_id=summary_versions[1].id,
    )
    assert comparison is not None
    assert comparison.selected.content == "Summary v2"
    assert comparison.predecessor is not None
    assert comparison.predecessor.content == "Summary v1"

    other_case = await service.create_case(
        CaseCreate(
            summary="Other summary",
            description="Other description",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    assert (
        await service.versions.compare_with_predecessor(
            case_id=other_case.id,
            version_id=summary_versions[1].id,
        )
        is None
    )


async def test_restore_case_version_is_scoped_append_only_and_atomic(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Restore updates one field, appends history, and rolls back with activity."""
    assert svc_role.user_id is not None
    session.add(
        User(
            id=svc_role.user_id,
            email=f"case-restore-{svc_role.user_id}@example.com",
            hashed_password="hashed",
        )
    )
    await session.flush()

    service = CasesService(session=session, role=svc_role)
    case = await service.create_case(
        CaseCreate(
            summary="Summary v1",
            description="Description v1",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    await service.update_case(
        case,
        CaseUpdate(summary="Summary v2", description="Description v2"),
    )
    summary_v1 = await session.scalar(
        select(CaseVersion).where(
            CaseVersion.case_id == case.id,
            CaseVersion.field == CaseVersionField.SUMMARY,
            CaseVersion.version == 1,
        )
    )
    assert summary_v1 is not None
    event_count_before = await session.scalar(
        select(func.count()).select_from(CaseEvent).where(CaseEvent.case_id == case.id)
    )
    assert event_count_before is not None

    restored = await service.restore_version(
        case_id=case.id,
        version_id=summary_v1.id,
    )
    assert restored.restored is True
    assert restored.field == CaseVersionField.SUMMARY

    await session.refresh(case)
    assert case.summary == "Summary v1"
    assert case.description == "Description v2"
    summary_history = (
        (
            await session.execute(
                select(CaseVersion)
                .where(
                    CaseVersion.case_id == case.id,
                    CaseVersion.field == CaseVersionField.SUMMARY,
                )
                .order_by(CaseVersion.version)
            )
        )
        .scalars()
        .all()
    )
    assert [(item.version, item.content) for item in summary_history] == [
        (1, "Summary v1"),
        (2, "Summary v2"),
        (3, "Summary v1"),
    ]
    assert summary_history[-1].user_id == svc_role.user_id
    assert summary_history[0].content == "Summary v1"
    restored_case_id = case.id
    summary_v2_id = summary_history[1].id
    assert (
        await session.scalar(
            select(func.count())
            .select_from(CaseEvent)
            .where(CaseEvent.case_id == case.id)
        )
        == event_count_before + 1
    )
    await service.restore_version(
        case_id=restored_case_id,
        version_id=summary_v1.id,
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(CaseVersion)
            .where(
                CaseVersion.case_id == restored_case_id,
                CaseVersion.field == CaseVersionField.SUMMARY,
            )
        )
        == 4
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(CaseEvent)
            .where(CaseEvent.case_id == restored_case_id)
        )
        == event_count_before + 2
    )

    other_case = await service.create_case(
        CaseCreate(
            summary="Other summary",
            description="Other description",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    other_version_id = await session.scalar(
        select(CaseVersion.id).where(
            CaseVersion.case_id == other_case.id,
            CaseVersion.field == CaseVersionField.SUMMARY,
        )
    )
    assert other_version_id is not None
    with pytest.raises(TracecatNotFoundError):
        await service.restore_version(
            case_id=restored_case_id,
            version_id=other_version_id,
        )

    foreign_workspace_id = uuid.uuid4()
    foreign_case_id = uuid.uuid4()
    foreign_version_id = uuid.uuid4()
    assert svc_role.organization_id is not None
    session.add_all(
        [
            Workspace(
                id=foreign_workspace_id,
                name="foreign-case-version-workspace",
                organization_id=svc_role.organization_id,
            ),
            Case(
                id=foreign_case_id,
                workspace_id=foreign_workspace_id,
                case_number=1,
                summary="Foreign summary",
                description="Foreign description",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            ),
            CaseVersion(
                id=foreign_version_id,
                workspace_id=foreign_workspace_id,
                case_id=foreign_case_id,
                field=CaseVersionField.SUMMARY,
                version=1,
                content="Foreign summary",
            ),
        ]
    )
    await session.commit()
    with pytest.raises(TracecatNotFoundError):
        await service.restore_version(
            case_id=restored_case_id,
            version_id=foreign_version_id,
        )

    failing_service = CasesService(session=session, role=svc_role)
    with (
        patch.object(
            failing_service.events,
            "create_event",
            new=AsyncMock(side_effect=RuntimeError("activity write failed")),
        ),
        pytest.raises(RuntimeError, match="activity write failed"),
    ):
        await failing_service.restore_version(
            case_id=restored_case_id,
            version_id=summary_v2_id,
        )

    await session.refresh(case)
    assert case.summary == "Summary v1"
    assert (
        await session.scalar(
            select(func.count())
            .select_from(CaseVersion)
            .where(
                CaseVersion.case_id == restored_case_id,
                CaseVersion.field == CaseVersionField.SUMMARY,
            )
        )
        == 4
    )
    test_workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert test_workspace is not None
    await session.refresh(test_workspace)
