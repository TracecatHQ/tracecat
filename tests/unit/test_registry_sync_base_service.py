from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import Organization
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionOptions,
    RegistryActionUDFImpl,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.sync.base_service import ArtifactsBuildResult
from tracecat.registry.sync.service import RegistrySyncService
from tracecat.registry.versions.service import RegistryVersionsService


def _make_action(
    *,
    repository_id: uuid.UUID,
    default_title: str,
) -> RegistryActionCreate:
    return RegistryActionCreate(
        repository_id=repository_id,
        name="reshape",
        description="Reshape test payload",
        namespace="core.transform",
        type="udf",
        origin=DEFAULT_REGISTRY_ORIGIN,
        interface={"expects": {}, "returns": {}},
        implementation=RegistryActionUDFImpl(
            type="udf",
            url=DEFAULT_REGISTRY_ORIGIN,
            module="tracecat_registry.core.transform",
            name="reshape",
        ),
        secrets=None,
        default_title=default_title,
        display_group=None,
        doc_url=None,
        author=None,
        deprecated=None,
        options=RegistryActionOptions(),
    )


@pytest.mark.anyio
async def test_sync_creates_collision_version_for_manifest_changes(
    session: AsyncSession,
    mock_org_id: uuid.UUID,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync should stay idempotent, but handle same-version content changes."""
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)

    session.add(
        Organization(
            id=mock_org_id,
            name="Sync Test Org",
            slug=f"sync-test-{mock_org_id.hex[:8]}",
            is_active=True,
        )
    )
    await session.flush()

    role = Role(
        type="service",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )

    repos_service = RegistryReposService(session, role)
    repo = await repos_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_REGISTRY_ORIGIN)
    )
    repo_id = repo.id

    first_actions = [_make_action(repository_id=repo_id, default_title="Old title")]
    second_actions = [_make_action(repository_id=repo_id, default_title="New title")]

    mocker.patch(
        "tracecat.registry.sync.base_service.fetch_actions_from_subprocess",
        side_effect=[
            SimpleNamespace(actions=first_actions, commit_sha=None),
            SimpleNamespace(actions=second_actions, commit_sha=None),
            SimpleNamespace(actions=second_actions, commit_sha=None),
        ],
    )

    async def fake_build_and_upload_artifacts(
        _self: RegistrySyncService,
        *,
        origin: str,
        version_string: str,
        commit_sha: str | None,
        ssh_env=None,
    ) -> ArtifactsBuildResult:
        del origin, commit_sha, ssh_env
        return ArtifactsBuildResult(
            tarball_uri=f"s3://test-bucket/{version_string}/site-packages.tar.gz"
        )

    mocker.patch.object(
        RegistrySyncService,
        "_build_and_upload_artifacts",
        side_effect=fake_build_and_upload_artifacts,
        autospec=True,
    )

    sync_service = RegistrySyncService(session, role)
    first = await sync_service.sync_repository_v2(
        repo, target_version="1.2.3", commit=False
    )
    second = await sync_service.sync_repository_v2(
        repo, target_version="1.2.3", commit=False
    )
    third = await sync_service.sync_repository_v2(
        repo, target_version="1.2.3", commit=False
    )

    assert first.version.version == "1.2.3"
    assert first.version.id != second.version.id
    assert second.version.version.startswith("1.2.3.dev")
    # Re-syncing unchanged content should reuse the active collision version.
    assert third.version.id == second.version.id
    assert repo.current_version_id == second.version.id

    versions_service = RegistryVersionsService(session, role)
    versions = await versions_service.list_versions(repository_id=repo.id)
    assert len(versions) == 2
