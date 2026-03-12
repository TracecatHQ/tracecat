from __future__ import annotations

from inspect import unwrap

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Organization, RegistryRepository
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories import router as registry_repos_router
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_create_registry_repository_rejects_platform_builtin_origin(
    session: AsyncSession,
    test_role: Role,
) -> None:
    """Org-scoped registry API must not recreate the platform builtin origin."""
    create_repository = unwrap(registry_repos_router.create_registry_repository)

    with pytest.raises(HTTPException) as exc_info:
        await create_repository(
            role=test_role,
            session=session,
            params=RegistryRepositoryCreate(origin=DEFAULT_REGISTRY_ORIGIN),
        )

    assert exc_info.value.status_code == 400
    assert "platform-scoped" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_update_registry_repository_rejects_platform_builtin_origin(
    session: AsyncSession,
    test_role: Role,
) -> None:
    """Org-scoped registry API must not mutate a repo into the builtin origin."""
    update_repository = unwrap(registry_repos_router.update_registry_repository)

    session.add(
        Organization(
            id=test_role.organization_id,
            name="Registry router test org",
            slug="registry-router-test-org",
            is_active=True,
        )
    )
    await session.flush()

    repository = RegistryRepository(
        organization_id=test_role.organization_id,
        origin=DEFAULT_LOCAL_REGISTRY_ORIGIN,
    )
    session.add(repository)
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await update_repository(
            role=test_role,
            session=session,
            repository_id=repository.id,
            params=RegistryRepositoryUpdate(origin=DEFAULT_REGISTRY_ORIGIN),
        )

    assert exc_info.value.status_code == 400
    assert "platform-scoped" in str(exc_info.value.detail)
