from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import Organization, PlatformRegistryVersion, RegistryRepository
from tracecat.exceptions import TracecatNotFoundError
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionOptions,
    RegistryActionUDFImpl,
)
from tracecat.registry.artifact_keys import get_artifact_local_dir
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
    REGISTRY_GIT_SSH_KEY_SECRET_NAME,
)
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.sync.artifact import RegistryArtifactBuildResult
from tracecat.registry.sync.base_service import ArtifactsBuildResult
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.registry.sync.prebuilt import write_prebuilt_registry_manifest
from tracecat.registry.sync.runner import RegistrySyncValidationError
from tracecat.registry.sync.service import RegistrySyncError, RegistrySyncService
from tracecat.registry.versions.schemas import RegistryVersionManifest
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


def _make_artifact_result(tmp_path: Path) -> RegistryArtifactBuildResult:
    squashfs_path = tmp_path / "site-packages.squashfs"
    squashfs_path.write_bytes(b"squashfs")
    return RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name="site-packages.squashfs",
        content_hash="hash",
        artifact_size_bytes=len(b"squashfs"),
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
            SimpleNamespace(
                actions=first_actions, commit_sha=None, validation_errors={}
            ),
            SimpleNamespace(
                actions=second_actions, commit_sha=None, validation_errors={}
            ),
            SimpleNamespace(
                actions=second_actions, commit_sha=None, validation_errors={}
            ),
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
            artifact_uri=f"s3://test-bucket/{version_string}/site-packages.squashfs"
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


@pytest.mark.anyio
async def test_platform_builtin_sync_reuses_existing_artifact_objects(
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "registry")
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True)

    ensure_bucket_exists = mocker.patch(
        "tracecat.registry.sync.base_service.blob.ensure_bucket_exists",
        mocker.AsyncMock(),
    )
    file_exists = mocker.patch(
        "tracecat.registry.sync.base_service.blob.file_exists",
        mocker.AsyncMock(return_value=True),
    )
    build_builtin_registry_artifact = mocker.patch(
        "tracecat.registry.sync.base_service.build_builtin_registry_artifact",
        mocker.AsyncMock(),
    )
    upload_squashfs_venv = mocker.patch(
        "tracecat.registry.sync.base_service.upload_squashfs_venv",
        mocker.AsyncMock(),
    )

    service = PlatformRegistrySyncService(mocker.Mock(spec=AsyncSession))

    result = await service._build_and_upload_artifacts(
        origin=DEFAULT_REGISTRY_ORIGIN,
        version_string="1.2.3",
        commit_sha=None,
    )

    assert result.artifact_uri == (
        "s3://registry/platform/tarball-venvs/tracecat_registry/1.2.3/"
        "site-packages.squashfs"
    )
    ensure_bucket_exists.assert_awaited_once_with("registry")
    file_exists.assert_awaited_once_with(
        key="platform/tarball-venvs/tracecat_registry/1.2.3/site-packages.squashfs",
        bucket="registry",
    )
    build_builtin_registry_artifact.assert_not_awaited()
    upload_squashfs_venv.assert_not_awaited()


@pytest.mark.anyio
async def test_platform_builtin_sync_uploads_squashfs_artifact(
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "registry")
    artifact_result = _make_artifact_result(tmp_path)

    mocker.patch(
        "tracecat.registry.sync.base_service.blob.ensure_bucket_exists",
        mocker.AsyncMock(),
    )
    mocker.patch(
        "tracecat.registry.sync.base_service.blob.file_exists",
        mocker.AsyncMock(return_value=False),
    )
    build_builtin_registry_artifact = mocker.patch(
        "tracecat.registry.sync.base_service.build_builtin_registry_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    upload_squashfs_venv = mocker.patch(
        "tracecat.registry.sync.base_service.upload_squashfs_venv",
        mocker.AsyncMock(
            return_value=(
                "s3://registry/platform/tarball-venvs/tracecat_registry/1.2.3/"
                "site-packages.squashfs"
            )
        ),
    )

    service = PlatformRegistrySyncService(mocker.Mock(spec=AsyncSession))

    result = await service._build_and_upload_artifacts(
        origin=DEFAULT_REGISTRY_ORIGIN,
        version_string="1.2.3",
        commit_sha=None,
    )

    assert result.artifact_uri.endswith("/1.2.3/site-packages.squashfs")
    build_builtin_registry_artifact.assert_awaited_once()
    upload_squashfs_venv.assert_awaited_once_with(
        squashfs_path=artifact_result.squashfs_path,
        key="platform/tarball-venvs/tracecat_registry/1.2.3/site-packages.squashfs",
        bucket="registry",
    )


@pytest.mark.anyio
async def test_platform_builtin_sync_uses_prebuilt_manifest_without_discovery(
    session: AsyncSession,
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "registry")
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )

    repos_service = PlatformRegistryReposService(session)
    repo = await repos_service.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)
    manifest = RegistryVersionManifest.from_actions(
        [_make_action(repository_id=repo.id, default_title="Prebuilt title")]
    )
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    write_prebuilt_registry_manifest(artifact_dir=artifact_dir, manifest=manifest)

    fetch_actions_from_subprocess = mocker.patch(
        "tracecat.registry.sync.base_service.fetch_actions_from_subprocess",
        mocker.AsyncMock(),
    )
    mocker.patch.object(
        PlatformRegistrySyncService,
        "_build_and_upload_artifacts",
        mocker.AsyncMock(),
    )

    sync_service = PlatformRegistrySyncService(session)
    result = await sync_service.sync_repository_v2(
        repo,
        target_version="1.2.3",
        bypass_temporal=True,
        defer_artifact_build=True,
        commit=False,
    )

    assert result.num_actions == 1
    assert result.actions[0].default_title == "Prebuilt title"
    assert result.artifact_uri.endswith("/1.2.3/site-packages.squashfs")
    assert RegistryVersionManifest.model_validate(result.version.manifest) == manifest
    fetch_actions_from_subprocess.assert_not_awaited()


@pytest.mark.anyio
async def test_platform_builtin_sync_falls_back_when_prebuilt_manifest_conversion_fails(
    session: AsyncSession,
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "registry")
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )

    repos_service = PlatformRegistryReposService(session)
    repo = await repos_service.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)
    prebuilt_manifest = RegistryVersionManifest.from_actions(
        [_make_action(repository_id=repo.id, default_title="Prebuilt title")]
    )
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    write_prebuilt_registry_manifest(
        artifact_dir=artifact_dir,
        manifest=prebuilt_manifest,
    )

    fallback_action = _make_action(
        repository_id=repo.id,
        default_title="Recovered title",
    )
    fetch_actions_from_subprocess = mocker.patch(
        "tracecat.registry.sync.base_service.fetch_actions_from_subprocess",
        mocker.AsyncMock(
            return_value=SimpleNamespace(
                actions=[fallback_action],
                commit_sha=None,
                validation_errors={},
            )
        ),
    )

    def raise_conversion_error(*_args, **_kwargs) -> list[RegistryActionCreate]:
        raise ValueError("bad prebuilt action payload")

    monkeypatch.setattr(
        RegistryVersionManifest,
        "to_action_creates",
        raise_conversion_error,
    )

    sync_service = PlatformRegistrySyncService(session)
    result = await sync_service.sync_repository_v2(
        repo,
        target_version="1.2.3",
        bypass_temporal=True,
        defer_artifact_build=True,
        commit=False,
    )

    assert result.num_actions == 1
    assert result.actions[0].default_title == "Recovered title"
    assert result.artifact_uri.endswith("/1.2.3/site-packages.squashfs")
    assert RegistryVersionManifest.model_validate(
        result.version.manifest
    ) == RegistryVersionManifest.from_actions([fallback_action])
    fetch_actions_from_subprocess.assert_awaited_once()


@pytest.mark.anyio
async def test_platform_builtin_sync_can_create_pending_version(
    session: AsyncSession,
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "registry")
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )

    repos_service = PlatformRegistryReposService(session)
    repo = await repos_service.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)
    repo.current_version_id = None
    session.add(repo)
    await session.flush()
    await session.execute(
        delete(PlatformRegistryVersion).where(
            PlatformRegistryVersion.repository_id == repo.id
        )
    )
    await session.flush()

    manifest = RegistryVersionManifest.from_actions(
        [_make_action(repository_id=repo.id, default_title="Prebuilt title")]
    )
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    write_prebuilt_registry_manifest(artifact_dir=artifact_dir, manifest=manifest)

    mocker.patch(
        "tracecat.registry.sync.base_service.fetch_actions_from_subprocess",
        mocker.AsyncMock(),
    )
    mocker.patch.object(
        PlatformRegistrySyncService,
        "_build_and_upload_artifacts",
        mocker.AsyncMock(),
    )

    sync_service = PlatformRegistrySyncService(session)
    result = await sync_service.sync_repository_v2(
        repo,
        target_version="1.2.3",
        bypass_temporal=True,
        defer_artifact_build=True,
        promote=False,
        commit=False,
    )

    assert result.version.version == "1.2.3"
    assert repo.current_version_id is None


@pytest.mark.anyio
async def test_sync_via_temporal_matches_validation_application_error_before_cause_walk(
    session: AsyncSession,
    mock_org_id: uuid.UUID,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", True)

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

    validation_error = RegistrySyncValidationError(
        {
            "tracecat.examples.broken": [],
        }
    )
    try:
        raise ApplicationError(
            str(validation_error),
            non_retryable=True,
            type="RegistrySyncValidationError",
        ) from validation_error
    except ApplicationError as app_error:
        workflow_error = WorkflowFailureError(cause=app_error)

    mock_client = mocker.Mock()
    mock_client.execute_workflow = mocker.AsyncMock(side_effect=workflow_error)
    mocker.patch(
        "tracecat.dsl.client.get_temporal_client",
        mocker.AsyncMock(return_value=mock_client),
    )

    sync_service = RegistrySyncService(session, role)

    with pytest.raises(
        RegistrySyncError,
        match="RegistrySyncValidationError: Registry sync validation failed",
    ) as exc_info:
        await sync_service.sync_repository_v2(repo, commit=False)

    assert "workflow failed" not in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_sync_via_temporal_git_requires_registry_ssh_key_before_workflow(
    mock_org_id: uuid.UUID,
    mocker,
) -> None:
    role = Role(
        type="service",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )

    session = mocker.Mock(spec=AsyncSession)
    repo = RegistryRepository(
        id=uuid.uuid4(),
        origin="git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        organization_id=mock_org_id,
        current_version_id=None,
    )

    mock_client = mocker.Mock()
    mock_client.execute_workflow = mocker.AsyncMock()
    get_temporal_client = mocker.patch(
        "tracecat.dsl.client.get_temporal_client",
        mocker.AsyncMock(return_value=mock_client),
    )
    get_org_secret_by_name = mocker.patch(
        "tracecat.registry.sync.base_service.SecretsService.get_org_secret_by_name",
        mocker.AsyncMock(side_effect=TracecatNotFoundError("missing")),
    )
    get_ssh_key = mocker.patch(
        "tracecat.registry.sync.base_service.SecretsService.get_ssh_key",
        mocker.AsyncMock(),
    )

    sync_service = RegistrySyncService(session, role)

    with pytest.raises(RegistrySyncError, match=REGISTRY_GIT_SSH_KEY_SECRET_NAME):
        await sync_service._sync_via_temporal_workflow(repo, commit=False)

    get_org_secret_by_name.assert_awaited_once_with(REGISTRY_GIT_SSH_KEY_SECRET_NAME)
    get_ssh_key.assert_not_awaited()
    get_temporal_client.assert_not_awaited()
    mock_client.execute_workflow.assert_not_awaited()


@pytest.mark.anyio
async def test_sync_via_temporal_git_preserves_unexpected_secret_check_error(
    mock_org_id: uuid.UUID,
    mocker,
) -> None:
    role = Role(
        type="service",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )

    session = mocker.Mock(spec=AsyncSession)
    repo = RegistryRepository(
        id=uuid.uuid4(),
        origin="git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        organization_id=mock_org_id,
        current_version_id=None,
    )

    get_temporal_client = mocker.patch(
        "tracecat.dsl.client.get_temporal_client",
        mocker.AsyncMock(),
    )
    get_org_secret_by_name = mocker.patch(
        "tracecat.registry.sync.base_service.SecretsService.get_org_secret_by_name",
        mocker.AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    sync_service = RegistrySyncService(session, role)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await sync_service._sync_via_temporal_workflow(repo, commit=False)

    get_org_secret_by_name.assert_awaited_once_with(REGISTRY_GIT_SSH_KEY_SECRET_NAME)
    get_temporal_client.assert_not_awaited()
