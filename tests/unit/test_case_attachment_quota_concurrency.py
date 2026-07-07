from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.db.models import Base, Case, CaseAttachment, Organization, Workspace
from tracecat.storage.exceptions import MaxAttachmentsExceededError

TEST_ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _service_role(*, workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=TEST_ORG_ID,
        service_id="tracecat-api",
        scopes=frozenset({"case:update"}),
    )


def _postgres_base_url() -> str:
    return (
        f"postgresql+asyncpg://postgres:postgres@localhost:"
        f"{os.environ.get('PG_PORT', '5432')}/"
    )


def _terminate_database_connections(conn: sa.Connection, database_name: str) -> None:
    conn.execute(
        text(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = :database_name
            AND pid <> pg_backend_pid()
            """
        ),
        {"database_name": database_name},
    )


@pytest.fixture
def attachment_quota_db_url() -> Iterator[str]:
    database_name = f"test_case_attachment_quota_{uuid.uuid4().hex}"
    base_url = _postgres_base_url()
    sys_url_sync = f"{base_url}postgres".replace("+asyncpg", "+psycopg")
    test_url = f"{base_url}{database_name}"
    test_url_sync = test_url.replace("+asyncpg", "+psycopg")

    sys_engine = create_engine(sys_url_sync, isolation_level="AUTOCOMMIT")
    test_engine = None
    try:
        with sys_engine.connect() as conn:
            _terminate_database_connections(conn, database_name)
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))

        test_engine = create_engine(test_url_sync)
        with test_engine.begin() as conn:
            Base.metadata.create_all(conn)

        yield test_url
    finally:
        if test_engine is not None:
            test_engine.dispose()
        with sys_engine.connect() as conn:
            _terminate_database_connections(conn, database_name)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        sys_engine.dispose()


async def _load_case(session: AsyncSession, case_id: uuid.UUID) -> Case:
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalars().one()
    return case


@pytest.mark.anyio
async def test_concurrent_case_uploads_do_not_exceed_attachment_limit(
    attachment_quota_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    first_content = b"first concurrent upload"
    second_content = b"second concurrent upload"

    engine = create_async_engine(
        attachment_quota_db_url,
        isolation_level="READ COMMITTED",
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(Organization).values(
                    id=TEST_ORG_ID,
                    name="Test Organization",
                    slug=f"test-org-{uuid.uuid4().hex[:8]}",
                    is_active=True,
                )
            )
            await conn.execute(
                sa.insert(Workspace).values(
                    id=workspace_id,
                    organization_id=TEST_ORG_ID,
                    name="Attachment Quota Race Workspace",
                    settings={"validate_attachment_magic_number": False},
                )
            )
            await conn.execute(
                sa.insert(Case).values(
                    id=case_id,
                    workspace_id=workspace_id,
                    case_number=1,
                    summary="Attachment quota race",
                    description="Concurrent upload quota regression",
                    status=CaseStatus.NEW,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.LOW,
                )
            )

        monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENTS_PER_CASE", 1)

        first_upload_paused = asyncio.Event()
        release_first_upload = asyncio.Event()
        second_lock_attempted = asyncio.Event()
        lock_calls = 0
        original_lock = CaseAttachmentService._lock_case_for_attachment_write

        async def lock_case_for_attachment_write(
            self: CaseAttachmentService, case: Case
        ) -> Case:
            nonlocal lock_calls
            lock_calls += 1
            if lock_calls == 2:
                second_lock_attempted.set()
            return await original_lock(self, case)

        async def upload_to_attachments_bucket(
            self: CaseAttachmentService,
            *,
            content: bytes,
            sha256: str,
            content_type: str,
        ) -> None:
            _ = (self, sha256, content_type)
            if content == first_content:
                first_upload_paused.set()
                await release_first_upload.wait()

        monkeypatch.setattr(
            CaseAttachmentService,
            "_lock_case_for_attachment_write",
            lock_case_for_attachment_write,
        )
        monkeypatch.setattr(
            CaseAttachmentService,
            "_upload_to_attachments_bucket",
            upload_to_attachments_bucket,
        )

        async def emit_case_event(*args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        monkeypatch.setattr(
            CaseAttachmentService,
            "_emit_case_event",
            emit_case_event,
        )

        async def create_attachment(content: bytes, filename: str) -> CaseAttachment:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                service = CaseAttachmentService(
                    session=session,
                    role=_service_role(workspace_id=workspace_id),
                )
                case = await _load_case(session, case_id)
                return await service.create_attachment(
                    case,
                    CaseAttachmentCreate(
                        file_name=filename,
                        content_type="text/plain",
                        size=len(content),
                        content=content,
                    ),
                )

        first_task = asyncio.create_task(create_attachment(first_content, "first.txt"))
        await asyncio.wait_for(first_upload_paused.wait(), timeout=5)

        second_task = asyncio.create_task(
            create_attachment(second_content, "second.txt")
        )
        await asyncio.wait_for(second_lock_attempted.wait(), timeout=5)

        release_first_upload.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True),
            timeout=10,
        )

        successes = [result for result in results if isinstance(result, CaseAttachment)]
        limit_errors = [
            result
            for result in results
            if isinstance(result, MaxAttachmentsExceededError)
        ]
        assert len(successes) == 1
        assert len(limit_errors) == 1

        async with AsyncSession(engine, expire_on_commit=False) as session:
            count_result = await session.execute(
                select(func.count())
                .select_from(CaseAttachment)
                .where(CaseAttachment.case_id == case_id)
            )
            assert count_result.scalar_one() == 1
    finally:
        await engine.dispose()
