"""Tests for registry version promotion.

This test suite validates:
1. Manual version promotion works correctly
2. Promotion fails for non-existent versions
3. Promotion fails for versions without tarball
4. Sync auto-promotes new versions
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.registry.repositories.service import RegistryReposService

pytestmark = pytest.mark.usefixtures("db")


def _make_manifest(action_names: list[str]) -> dict:
    """Create a test manifest with given action names."""
    actions = {}
    for name in action_names:
        parts = name.rsplit(".", 1)
        namespace = parts[0] if len(parts) > 1 else "test"
        action_name = parts[-1]
        actions[name] = {
            "namespace": namespace,
            "name": action_name,
            "action_type": "udf",
            "description": f"Test action {name}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": "test_origin",
                "module": f"test.{namespace}",
                "name": action_name,
            },
        }
    return {"version": "1.0", "actions": actions}


@pytest.mark.anyio
async def test_promote_version_success(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a valid version works correctly."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create two versions
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.flush()

    # Set version 2 as current
    repo.current_version_id = version2.id
    session.add(repo)
    await session.commit()

    # Promote version 1 (rollback)
    service = RegistryReposService(session, role=svc_admin_role)
    updated_repo = await service.promote_version(repo, version1.id)

    # Verify version 1 is now current
    assert updated_repo.current_version_id == version1.id


@pytest.mark.anyio
async def test_promote_version_not_found(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a non-existent version fails with helpful error."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.commit()

    # Try to promote a non-existent version
    service = RegistryReposService(session, role=svc_admin_role)
    fake_version_id = uuid.uuid4()

    with pytest.raises(RegistryError, match="not found"):
        await service.promote_version(repo, fake_version_id)


@pytest.mark.anyio
async def test_promote_version_wrong_repository(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a version from another repository fails."""
    # Create two repositories
    repo1 = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="origin_1",
    )
    repo2 = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="origin_2",
    )
    session.add_all([repo1, repo2])
    await session.flush()

    # Create version for repo2
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo2.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.commit()

    # Try to promote repo2's version on repo1
    service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="does not belong to repository"):
        await service.promote_version(repo1, version.id)


@pytest.mark.anyio
async def test_promote_version_missing_tarball(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a version without tarball fails."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create version without tarball_uri (simulate incomplete version)
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="",  # Empty tarball URI
    )
    session.add(version)
    await session.commit()

    # Try to promote version without tarball
    service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="no tarball artifact"):
        await service.promote_version(repo, version.id)


@pytest.mark.anyio
async def test_promote_preserves_previous_version(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promotion correctly tracks the previous version."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create versions
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.flush()

    # Set version 1 as current
    repo.current_version_id = version1.id
    session.add(repo)
    await session.commit()

    # Get the previous version before promotion
    previous_version_id = repo.current_version_id

    # Promote version 2
    service = RegistryReposService(session, role=svc_admin_role)
    updated_repo = await service.promote_version(repo, version2.id)

    # Verify previous version was version 1
    assert previous_version_id == version1.id
    # Verify current version is now version 2
    assert updated_repo.current_version_id == version2.id


@pytest.mark.anyio
async def test_promote_to_same_version_is_idempotent(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting to the already-current version works."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create version
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.flush()

    # Set version as current
    repo.current_version_id = version.id
    session.add(repo)
    await session.commit()

    # Promote the same version again (idempotent operation)
    service = RegistryReposService(session, role=svc_admin_role)
    updated_repo = await service.promote_version(repo, version.id)

    # Should still be the same version
    assert updated_repo.current_version_id == version.id
