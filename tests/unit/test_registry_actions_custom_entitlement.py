from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryIndex,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN

pytestmark = pytest.mark.usefixtures("db")


def _make_manifest(action_names: list[str], *, origin: str) -> dict:
    actions: dict[str, dict] = {}
    for action_name in action_names:
        namespace, name = action_name.rsplit(".", 1)
        actions[action_name] = {
            "namespace": namespace,
            "name": name,
            "action_type": "udf",
            "description": f"Test action {action_name}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": origin,
                "module": "test.module",
                "name": name,
            },
        }
    return {"version": "1.0", "actions": actions}


async def _seed_platform_registry(
    session: AsyncSession,
    *,
    origin: str,
    version: str,
    action_names: list[str],
) -> PlatformRegistryRepository:
    repo = await session.scalar(
        select(PlatformRegistryRepository).where(PlatformRegistryRepository.origin == origin)
    )
    if repo is None:
        repo = PlatformRegistryRepository(origin=origin)
        session.add(repo)
        await session.flush()

    registry_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version=version,
        manifest=_make_manifest(action_names, origin=origin),
        tarball_uri=f"s3://platform/{version}.tar.gz",
    )
    session.add(registry_version)
    await session.flush()

    repo.current_version_id = registry_version.id
    session.add(repo)

    for action_name in action_names:
        namespace, name = action_name.rsplit(".", 1)
        session.add(
            PlatformRegistryIndex(
                registry_version_id=registry_version.id,
                namespace=namespace,
                name=name,
                action_type="udf",
                description=f"Platform action {action_name}",
                options={"include_in_schema": True},
            )
        )
    await session.commit()
    return repo


async def _seed_org_registry(
    session: AsyncSession,
    *,
    role: Role,
    origin: str,
    version: str,
    action_names: list[str],
) -> RegistryRepository:
    repo = RegistryRepository(
        organization_id=role.organization_id,
        origin=origin,
    )
    session.add(repo)
    await session.flush()

    registry_version = RegistryVersion(
        organization_id=role.organization_id,
        repository_id=repo.id,
        version=version,
        manifest=_make_manifest(action_names, origin=origin),
        tarball_uri=f"s3://org/{version}.tar.gz",
    )
    session.add(registry_version)
    await session.flush()

    repo.current_version_id = registry_version.id
    session.add(repo)

    for action_name in action_names:
        namespace, name = action_name.rsplit(".", 1)
        session.add(
            RegistryIndex(
                organization_id=role.organization_id,
                registry_version_id=registry_version.id,
                namespace=namespace,
                name=name,
                action_type="udf",
                description=f"Org action {action_name}",
                options={"include_in_schema": True},
            )
        )
    await session.commit()
    return repo


@pytest.mark.anyio
async def test_index_list_hides_custom_actions_without_entitlement(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    shared_action = "acme.test.shared"
    custom_only_action = "acme.test.custom_only"
    custom_origin = "git+ssh://git@github.com/acme/custom-registry.git"

    await _seed_platform_registry(
        session,
        origin=DEFAULT_REGISTRY_ORIGIN,
        version="platform-1.0",
        action_names=[shared_action],
    )
    await _seed_org_registry(
        session,
        role=svc_role,
        origin=custom_origin,
        version="org-1.0",
        action_names=[shared_action, custom_only_action],
    )

    service = RegistryActionsService(session, role=svc_role)
    with patch.object(
        service, "has_entitlement", new=AsyncMock(return_value=False)
    ) as mock_has_entitlement:
        entries = await service.list_actions_from_index(namespace="acme.test")

    actions_to_origin = {f"{entry.namespace}.{entry.name}": origin for entry, origin in entries}
    assert actions_to_origin[shared_action] == DEFAULT_REGISTRY_ORIGIN
    assert custom_only_action not in actions_to_origin
    mock_has_entitlement.assert_awaited_once()


@pytest.mark.anyio
async def test_get_action_from_index_uses_platform_fallback_without_entitlement(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    shared_action = "acme.detail.shared"
    custom_only_action = "acme.detail.custom_only"
    custom_origin = "git+ssh://git@github.com/acme/custom-registry.git"

    await _seed_platform_registry(
        session,
        origin=DEFAULT_REGISTRY_ORIGIN,
        version="platform-1.0",
        action_names=[shared_action],
    )
    await _seed_org_registry(
        session,
        role=svc_role,
        origin=custom_origin,
        version="org-1.0",
        action_names=[shared_action, custom_only_action],
    )

    service = RegistryActionsService(session, role=svc_role)
    with patch.object(
        service, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        shared = await service.get_action_from_index(shared_action)
        custom_only = await service.get_action_from_index(custom_only_action)

    assert shared is not None
    assert shared.origin == DEFAULT_REGISTRY_ORIGIN
    assert custom_only is None


@pytest.mark.anyio
async def test_get_actions_from_index_filters_custom_and_keeps_platform_fallback(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    shared_action = "acme.batch.shared"
    custom_only_action = "acme.batch.custom_only"
    custom_origin = "git+ssh://git@github.com/acme/custom-registry.git"

    await _seed_platform_registry(
        session,
        origin=DEFAULT_REGISTRY_ORIGIN,
        version="platform-1.0",
        action_names=[shared_action],
    )
    await _seed_org_registry(
        session,
        role=svc_role,
        origin=custom_origin,
        version="org-1.0",
        action_names=[shared_action, custom_only_action],
    )

    service = RegistryActionsService(session, role=svc_role)
    with patch.object(
        service, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        results = await service.get_actions_from_index(
            [shared_action, custom_only_action]
        )

    assert set(results.keys()) == {shared_action}
    assert results[shared_action].origin == DEFAULT_REGISTRY_ORIGIN


@pytest.mark.anyio
async def test_list_actions_from_index_by_repository_returns_empty_for_custom_repo_without_entitlement(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    custom_origin = "git+ssh://git@github.com/acme/custom-registry.git"
    custom_repo = await _seed_org_registry(
        session,
        role=svc_role,
        origin=custom_origin,
        version="org-1.0",
        action_names=["acme.repo.only_action"],
    )

    service = RegistryActionsService(session, role=svc_role)
    with patch.object(
        service, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        actions = await service.list_actions_from_index_by_repository(custom_repo.id)

    assert actions == []


@pytest.mark.anyio
async def test_search_actions_from_index_hides_custom_actions_without_entitlement(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    shared_action = "acme.search.shared"
    custom_only_action = "acme.search.custom_only"
    custom_origin = "git+ssh://git@github.com/acme/custom-registry.git"

    await _seed_platform_registry(
        session,
        origin=DEFAULT_REGISTRY_ORIGIN,
        version="platform-1.0",
        action_names=[shared_action],
    )
    await _seed_org_registry(
        session,
        role=svc_role,
        origin=custom_origin,
        version="org-1.0",
        action_names=[shared_action, custom_only_action],
    )

    service = RegistryActionsService(session, role=svc_role)
    with patch.object(
        service, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        entries = await service.search_actions_from_index("acme.search")

    actions_to_origin = {f"{entry.namespace}.{entry.name}": origin for entry, origin in entries}
    assert actions_to_origin[shared_action] == DEFAULT_REGISTRY_ORIGIN
    assert custom_only_action not in actions_to_origin
