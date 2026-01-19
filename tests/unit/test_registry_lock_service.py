"""Tests for RegistryLockService.

This test suite validates:
1. Lock resolution uses current_version_id, not newest row wins
2. Platform registries are queried and available to orgs
3. Org registries override platform for the same origin
4. Helpful error when no current_version is set
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.exceptions import RegistryError
from tracecat.registry.lock.service import RegistryLockService

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
async def test_resolve_lock_uses_current_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock resolution uses current_version_id, not newest row wins."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create version 1 with action_a
    version1 = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_a"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version1)
    await session.flush()

    # Create version 2 with action_b (newer, but not current)
    version2 = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_b"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add(version2)
    await session.flush()

    # Set version 1 as current (not the newest)
    repo.current_version_id = version1.id
    session.add(repo)
    await session.commit()

    # Resolve lock for action_a (in v1)
    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"test.action_a"})

    # Should use version 1 (the current version), not version 2
    assert lock.origins["test_origin"] == "1.0.0"
    assert "test.action_a" in lock.actions

    # action_b should NOT be found because version 2 is not current
    with pytest.raises(RegistryError, match="not found in any registry"):
        await service.resolve_lock_with_bindings({"test.action_b"})


@pytest.mark.anyio
async def test_resolve_lock_queries_platform_registry(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that platform registries are available to organizations."""
    # Create a platform repository with action
    platform_repo = PlatformRegistryRepository(
        origin="platform_registry",
    )
    session.add(platform_repo)
    await session.flush()

    platform_version = PlatformRegistryVersion(
        repository_id=platform_repo.id,
        version="1.0.0",
        manifest=_make_manifest(["platform.shared_action"]),
        tarball_uri="s3://platform/v1.tar.gz",
    )
    session.add(platform_version)
    await session.flush()

    # Set as current version
    platform_repo.current_version_id = platform_version.id
    session.add(platform_repo)
    await session.commit()

    # Resolve lock - org should see platform actions
    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"platform.shared_action"})

    assert "platform_registry" in lock.origins
    assert lock.origins["platform_registry"] == "1.0.0"
    assert lock.actions["platform.shared_action"] == "platform_registry"


@pytest.mark.anyio
async def test_resolve_lock_org_overrides_platform(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that org registries take precedence over platform for same origin."""
    origin = "shared_origin"

    # Create platform repository
    platform_repo = PlatformRegistryRepository(origin=origin)
    session.add(platform_repo)
    await session.flush()

    platform_version = PlatformRegistryVersion(
        repository_id=platform_repo.id,
        version="platform-1.0",
        manifest=_make_manifest(["shared.action"]),
        tarball_uri="s3://platform/v1.tar.gz",
    )
    session.add(platform_version)
    await session.flush()

    platform_repo.current_version_id = platform_version.id
    session.add(platform_repo)

    # Create org repository with same origin
    org_repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin=origin,
    )
    session.add(org_repo)
    await session.flush()

    org_version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=org_repo.id,
        version="org-2.0",
        manifest=_make_manifest(["shared.action"]),
        tarball_uri="s3://org/v2.tar.gz",
    )
    session.add(org_version)
    await session.flush()

    org_repo.current_version_id = org_version.id
    session.add(org_repo)
    await session.commit()

    # Resolve lock - should use org version (overrides platform)
    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"shared.action"})

    # The org version should be used
    assert lock.origins[origin] == "org-2.0"


@pytest.mark.anyio
async def test_resolve_lock_fails_without_current_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock resolution fails with helpful error when no current_version is set."""
    # Create a repository without setting current_version_id
    repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create a version but don't set it as current
    version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.commit()

    # Note: current_version_id is still None

    # Resolve lock - should fail because action is not available
    service = RegistryLockService(session, role=svc_role)
    with pytest.raises(RegistryError, match="not found in any registry"):
        await service.resolve_lock_with_bindings({"test.action"})


@pytest.mark.anyio
async def test_resolve_lock_combines_platform_and_org(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock can contain actions from both platform and org registries."""
    # Create platform repository
    platform_repo = PlatformRegistryRepository(origin="platform_origin")
    session.add(platform_repo)
    await session.flush()

    platform_version = PlatformRegistryVersion(
        repository_id=platform_repo.id,
        version="1.0.0",
        manifest=_make_manifest(["platform.action"]),
        tarball_uri="s3://platform/v1.tar.gz",
    )
    session.add(platform_version)
    await session.flush()

    platform_repo.current_version_id = platform_version.id
    session.add(platform_repo)

    # Create org repository with different origin
    org_repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin="org_origin",
    )
    session.add(org_repo)
    await session.flush()

    org_version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=org_repo.id,
        version="2.0.0",
        manifest=_make_manifest(["org.action"]),
        tarball_uri="s3://org/v2.tar.gz",
    )
    session.add(org_version)
    await session.flush()

    org_repo.current_version_id = org_version.id
    session.add(org_repo)
    await session.commit()

    # Resolve lock for both actions
    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"platform.action", "org.action"})

    # Both should be in the lock
    assert "platform_origin" in lock.origins
    assert "org_origin" in lock.origins
    assert lock.actions["platform.action"] == "platform_origin"
    assert lock.actions["org.action"] == "org_origin"
