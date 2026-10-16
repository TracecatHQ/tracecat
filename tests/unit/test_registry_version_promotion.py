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


async def _seed_repo(
    session: AsyncSession,
    role: Role,
    versions: dict[str, str],
    *,
    origin: str = "test_origin",
    current: str | None = None,
) -> tuple[RegistryRepository, dict[str, RegistryVersion]]:
    """Create a repository with versions keyed by version string to tarball URI."""
    repo = RegistryRepository(organization_id=role.organization_id, origin=origin)
    session.add(repo)
    await session.flush()
    created = {
        version: RegistryVersion(
            organization_id=role.organization_id,
            repository_id=repo.id,
            version=version,
            manifest=_make_manifest(["test.action"]),
            tarball_uri=tarball_uri,
        )
        for version, tarball_uri in versions.items()
    }
    session.add_all(created.values())
    await session.flush()
    if current is not None:
        repo.current_version_id = created[current].id
        session.add(repo)
    await session.commit()
    return repo, created


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param("2.0.0", "1.0.0", id="rollback"),
        pytest.param("1.0.0", "2.0.0", id="upgrade"),
        pytest.param("1.0.0", "1.0.0", id="same_version_is_idempotent"),
    ],
)
async def test_promote_version_success(
    svc_admin_role: Role,
    session: AsyncSession,
    current: str,
    target: str,
) -> None:
    """Promoting a valid version makes it current."""
    repo, versions = await _seed_repo(
        session,
        svc_admin_role,
        {"1.0.0": "s3://test/v1.tar.gz", "2.0.0": "s3://test/v2.tar.gz"},
        current=current,
    )

    service = RegistryReposService(session, role=svc_admin_role)
    updated_repo = await service.promote_version(repo, versions[target].id)

    assert updated_repo.current_version_id == versions[target].id


@pytest.mark.anyio
async def test_promote_version_not_found(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a non-existent version fails with helpful error."""
    repo, _ = await _seed_repo(session, svc_admin_role, {})

    service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="not found"):
        await service.promote_version(repo, uuid.uuid4())


@pytest.mark.anyio
async def test_promote_version_wrong_repository(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a version from another repository fails."""
    repo1, _ = await _seed_repo(session, svc_admin_role, {}, origin="origin_1")
    _, versions = await _seed_repo(
        session, svc_admin_role, {"1.0.0": "s3://test/v1.tar.gz"}, origin="origin_2"
    )

    service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="does not belong to repository"):
        await service.promote_version(repo1, versions["1.0.0"].id)


@pytest.mark.anyio
async def test_promote_version_missing_execution_artifact(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that promoting a version without an execution artifact fails."""
    repo, versions = await _seed_repo(session, svc_admin_role, {"1.0.0": ""})

    service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="no execution artifact"):
        await service.promote_version(repo, versions["1.0.0"].id)
