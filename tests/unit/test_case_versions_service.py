"""Unit tests for case version services."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.service import CasesService
from tracecat.cases.versions.service import CaseVersionsService
from tracecat.exceptions import ScopeDeniedError
from tracecat.pagination import PageParams


def _role_with_scopes(*scopes: str) -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset(scopes),
    )


@pytest.mark.anyio
async def test_case_version_reads_require_case_read() -> None:
    """List, selected-version, and compare reads enforce case:read."""
    session = AsyncSession()
    service = CaseVersionsService(session, _role_with_scopes())
    case_id = uuid.uuid4()
    version_id = uuid.uuid4()
    try:
        with pytest.raises(ScopeDeniedError):
            await service.list_versions(
                case_id=case_id,
                page=PageParams(limit=1),
            )
        with pytest.raises(ScopeDeniedError):
            await service.get_version(
                case_id=case_id,
                version_id=version_id,
            )
        with pytest.raises(ScopeDeniedError):
            await service.compare_with_predecessor(
                case_id=case_id,
                version_id=version_id,
            )
    finally:
        await session.close()


@pytest.mark.anyio
async def test_case_version_restore_requires_case_update() -> None:
    """Restoring a version rejects a read-only role at the service boundary."""
    session = AsyncSession()
    service = CasesService(session, _role_with_scopes("case:read"))
    try:
        with pytest.raises(ScopeDeniedError):
            await service.restore_version(
                case_id=uuid.uuid4(),
                version_id=uuid.uuid4(),
            )
    finally:
        await session.close()
