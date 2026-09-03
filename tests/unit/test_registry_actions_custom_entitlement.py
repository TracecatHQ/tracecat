from __future__ import annotations

import uuid
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryIndex,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.registry.actions.service import (
    RegistryActionsService,
    _ActionMetadataRow,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.versions.schemas import RegistryVersionManifest

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
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == origin
        )
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

    actions_to_origin = {
        f"{entry.namespace}.{entry.name}": origin for entry, origin in entries
    }
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
    with patch.object(service, "has_entitlement", new=AsyncMock(return_value=False)):
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
    with patch.object(service, "has_entitlement", new=AsyncMock(return_value=False)):
        results = await service.get_actions_from_index(
            [shared_action, custom_only_action]
        )

    assert set(results.keys()) == {shared_action}
    assert results[shared_action].origin == DEFAULT_REGISTRY_ORIGIN


@pytest.mark.anyio
async def test_get_actions_from_index_reuses_manifest_for_same_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    action_names = ["acme.batch.first", "acme.batch.second"]
    await _seed_platform_registry(
        session,
        origin=DEFAULT_REGISTRY_ORIGIN,
        version="platform-shared-manifest",
        action_names=action_names,
    )

    service = RegistryActionsService(session, role=svc_role)
    results = await service.get_actions_from_index(action_names)

    assert set(results) == set(action_names)
    assert results[action_names[0]].manifest is results[action_names[1]].manifest


@pytest.mark.anyio
async def test_get_actions_from_index_retries_version_replaced_between_queries(
    svc_role: Role,
) -> None:
    action_name = "acme.batch.replaced"
    origin = f"git+ssh://git@github.com/acme/registry-{uuid.uuid4()}.git"
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        isolation_level="READ COMMITTED",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repository_id: uuid.UUID | None = None

    try:
        async with session_factory() as seed_session:
            repository = await _seed_org_registry(
                seed_session,
                role=svc_role,
                origin=origin,
                version="before-replacement",
                action_names=[action_name],
            )
            repository_id = repository.id
            old_version_id = repository.current_version_id
            assert old_version_id is not None

        async with session_factory() as read_session:
            service = RegistryActionsService(read_session, role=svc_role)
            original_load = service._load_action_manifests
            load_calls = 0

            async def replace_version_before_manifest_load(
                rows: Sequence[_ActionMetadataRow],
            ) -> dict[tuple[str, uuid.UUID], RegistryVersionManifest]:
                nonlocal load_calls
                load_calls += 1
                if load_calls == 1:
                    async with session_factory() as write_session:
                        repository = await write_session.scalar(
                            select(RegistryRepository).where(
                                RegistryRepository.id == repository_id
                            )
                        )
                        assert repository is not None
                        repository.current_version_id = None
                        await write_session.flush()
                        await write_session.execute(
                            delete(RegistryVersion).where(
                                RegistryVersion.id == old_version_id
                            )
                        )

                        replacement_manifest = _make_manifest(
                            [action_name], origin=origin
                        )
                        replacement_manifest["actions"][action_name]["description"] = (
                            "Replacement action"
                        )
                        replacement = RegistryVersion(
                            organization_id=svc_role.organization_id,
                            repository_id=repository.id,
                            version="after-replacement",
                            manifest=replacement_manifest,
                            tarball_uri="s3://org/after-replacement.tar.gz",
                        )
                        write_session.add(replacement)
                        await write_session.flush()
                        repository.current_version_id = replacement.id
                        write_session.add(
                            RegistryIndex(
                                organization_id=svc_role.organization_id,
                                registry_version_id=replacement.id,
                                namespace="acme.batch",
                                name="replaced",
                                action_type="udf",
                                description="Replacement action",
                                options={"include_in_schema": True},
                            )
                        )
                        await write_session.commit()

                return await original_load(rows)

            with (
                patch.object(
                    service,
                    "has_entitlement",
                    new=AsyncMock(return_value=True),
                ),
                patch.object(
                    service,
                    "_load_action_manifests",
                    new=replace_version_before_manifest_load,
                ),
            ):
                results = await service.get_actions_from_index([action_name])

        assert load_calls == 2
        assert results[action_name].index_entry.description == "Replacement action"
        assert (
            results[action_name].manifest.actions[action_name].description
            == "Replacement action"
        )
    finally:
        if repository_id is not None:
            async with session_factory() as cleanup_session:
                repository = await cleanup_session.scalar(
                    select(RegistryRepository).where(
                        RegistryRepository.id == repository_id
                    )
                )
                if repository is not None:
                    repository.current_version_id = None
                    await cleanup_session.flush()
                    await cleanup_session.delete(repository)
                    await cleanup_session.commit()
        await engine.dispose()


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
    with patch.object(service, "has_entitlement", new=AsyncMock(return_value=False)):
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
    with patch.object(service, "has_entitlement", new=AsyncMock(return_value=False)):
        entries = await service.search_actions_from_index("acme.search")

    actions_to_origin = {
        f"{entry.namespace}.{entry.name}": origin for entry, origin in entries
    }
    assert actions_to_origin[shared_action] == DEFAULT_REGISTRY_ORIGIN
    assert custom_only_action not in actions_to_origin
