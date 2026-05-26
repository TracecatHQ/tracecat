from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import RegistryError
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionOptions,
    RegistryActionUDFImpl,
    RegistryActionValidationErrorInfo,
)
from tracecat.registry.artifact_keys import get_artifact_local_dir
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync.artifact import RegistryArtifactBuildResult
from tracecat.registry.sync.prebuilt import write_prebuilt_registry_manifest
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    RegistrySyncRunner,
    RegistrySyncValidationError,
)
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.versions.schemas import RegistryVersionManifest


def _make_artifact_result(tmp_path: Path) -> RegistryArtifactBuildResult:
    squashfs_path = tmp_path / "site-packages.squashfs"
    squashfs_path.write_bytes(b"squashfs")
    return RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name="site-packages.squashfs",
        content_hash="hash",
        artifact_size_bytes=len(b"squashfs"),
    )


def test_write_prebuilt_registry_manifest_is_compact(tmp_path: Path) -> None:
    repository_id = uuid4()
    manifest = RegistryVersionManifest.from_actions(
        [
            RegistryActionCreate(
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
                default_title="Prebuilt title",
                display_group=None,
                doc_url=None,
                author=None,
                deprecated=None,
                options=RegistryActionOptions(),
            )
        ]
    )

    manifest_path = write_prebuilt_registry_manifest(
        artifact_dir=tmp_path,
        manifest=manifest,
    )

    manifest_text = manifest_path.read_text()
    assert "\n" not in manifest_text
    assert "  " not in manifest_text
    assert RegistryVersionManifest.model_validate_json(manifest_text) == manifest


def test_registry_sync_request_ignores_legacy_ssh_key() -> None:
    """Legacy SSH keys are accepted for rollout compatibility but not serialized."""
    payload = {
        "repository_id": str(uuid4()),
        "origin": "git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        "origin_type": "git",
        "git_url": "git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        "organization_id": str(uuid4()),
        "ssh_key": "fake-ssh-key",
    }

    request = RegistrySyncRequest.model_validate(payload)

    assert "ssh_key" not in request.model_dump()


@pytest.mark.anyio
async def test_runner_passes_resolved_commit_sha_to_discovery(
    tmp_path,
    mocker,
) -> None:
    runner = RegistrySyncRunner()
    organization_id = uuid4()
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        origin_type="git",
        git_url="git+ssh://git@github.com/TracecatHQ/internal-registry.git",
        commit_sha="requested-sha",
        organization_id=organization_id,
        validate_actions=True,
    )

    artifact_result = _make_artifact_result(tmp_path)

    clone_repository = mocker.patch.object(
        runner,
        "_clone_repository",
        mocker.AsyncMock(return_value=(tmp_path, "resolved-sha")),
    )
    fetch_registry_ssh_key = mocker.patch.object(
        runner,
        "_fetch_registry_ssh_key",
        mocker.AsyncMock(return_value="fake-ssh-key"),
    )
    mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    discover_actions = mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(return_value=([], {})),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(return_value="s3://registry/site-packages.squashfs"),
    )

    result = await runner.run(request)

    fetch_registry_ssh_key.assert_awaited_once_with(organization_id)
    clone_repository.assert_awaited_once_with(
        git_url=request.git_url,
        commit_sha="requested-sha",
        ssh_key="fake-ssh-key",
        work_dir=mocker.ANY,
    )
    discover_actions.assert_awaited_once_with(
        repository_id=request.repository_id,
        origin=request.origin,
        commit_sha="resolved-sha",
        validate=True,
        git_repo_package_name=None,
        organization_id=organization_id,
    )
    upload_tarball.assert_awaited_once()
    assert result.commit_sha == "resolved-sha"


@pytest.mark.anyio
async def test_fetch_registry_ssh_key_uses_role_scoped_service_session(mocker) -> None:
    runner = RegistrySyncRunner()
    organization_id = uuid4()
    secrets_service = mocker.Mock()
    secrets_service.get_ssh_key = mocker.AsyncMock(return_value=SecretStr("fake-key\n"))

    @asynccontextmanager
    async def fake_with_session(*, role: Role):
        assert role.organization_id == organization_id
        yield secrets_service

    with_session = mocker.patch(
        "tracecat.registry.sync.runner.SecretsService.with_session",
        side_effect=fake_with_session,
    )

    ssh_key = await runner._fetch_registry_ssh_key(organization_id)

    assert ssh_key == "fake-key\n"
    with_session.assert_called_once()
    secrets_service.get_ssh_key.assert_awaited_once_with(target="registry")


@pytest.mark.anyio
async def test_runner_raises_before_upload_on_validation_errors(
    tmp_path,
    mocker,
) -> None:
    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
        validate_actions=True,
    )

    artifact_result = _make_artifact_result(tmp_path)

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(
            return_value=(
                [],
                {
                    "tools.example.action": [
                        RegistryActionValidationErrorInfo(
                            type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
                            details=["Forbidden access to os.environ"],
                            is_template=False,
                            loc_primary="tools.example.action",
                            loc_secondary=None,
                        )
                    ]
                },
            )
        ),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(return_value="s3://registry/site-packages.squashfs"),
    )

    with pytest.raises(
        RegistrySyncValidationError,
        match="Registry sync validation failed: 1 validation error",
    ):
        await runner.run(request)

    upload_tarball.assert_not_awaited()


@pytest.mark.anyio
async def test_discover_actions_marks_template_load_errors_non_retryable(
    mocker,
) -> None:
    runner = RegistrySyncRunner()
    mocker.patch(
        "tracecat.registry.sync.runner.fetch_actions_from_subprocess",
        mocker.AsyncMock(
            side_effect=RegistryError(
                "Failed to load template action from "
                "/custom_actions/example_template.yml: invalid annotation"
            )
        ),
    )

    with pytest.raises(ActionDiscoveryError) as exc_info:
        await runner._discover_actions(
            repository_id=uuid4(),
            origin="tracecat_registry",
        )

    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_discover_actions_keeps_subprocess_errors_retryable(mocker) -> None:
    runner = RegistrySyncRunner()
    mocker.patch(
        "tracecat.registry.sync.runner.fetch_actions_from_subprocess",
        mocker.AsyncMock(
            side_effect=RegistryError(
                "Sync subprocess timed out after 300.0s for 'tracecat_registry'"
            )
        ),
    )

    with pytest.raises(ActionDiscoveryError) as exc_info:
        await runner._discover_actions(
            repository_id=uuid4(),
            origin="tracecat_registry",
        )

    assert exc_info.value.non_retryable is False


@pytest.mark.anyio
async def test_runner_does_not_upload_artifacts_under_target_version(
    tmp_path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )
    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin=DEFAULT_REGISTRY_ORIGIN,
        origin_type="builtin",
        target_version="1.2.3",
        storage_namespace="platform",
        validate_actions=True,
    )
    artifact_result = _make_artifact_result(tmp_path)

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(return_value=([], {})),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(return_value="s3://registry/generated/site-packages.squashfs"),
    )

    await runner.run(request)

    upload_tarball.assert_awaited_once_with(
        squashfs_path=artifact_result.squashfs_path,
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        commit_sha=None,
        storage_namespace="platform",
    )


@pytest.mark.anyio
async def test_runner_falls_back_to_discovery_when_prebuilt_manifest_is_invalid(
    tmp_path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )
    repository_id = uuid4()
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "manifest.json").write_text("{invalid")

    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=repository_id,
        origin=DEFAULT_REGISTRY_ORIGIN,
        origin_type="builtin",
        target_version="1.2.3",
        storage_namespace="platform",
        validate_actions=True,
    )

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    artifact_result = _make_artifact_result(tmp_path)
    build_tarball_venv = mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    discover_actions = mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(return_value=([], {})),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(
            return_value=(
                "s3://registry/platform/tarball-venvs/tracecat_registry/generated/"
                "site-packages.squashfs"
            )
        ),
    )

    result = await runner.run(request)

    assert result.tarball_uri.endswith("/site-packages.squashfs")
    build_tarball_venv.assert_awaited_once()
    discover_actions.assert_awaited_once_with(
        repository_id=repository_id,
        origin=DEFAULT_REGISTRY_ORIGIN,
        commit_sha=None,
        validate=True,
        git_repo_package_name=None,
        organization_id=None,
    )
    upload_tarball.assert_awaited_once()


@pytest.mark.anyio
async def test_runner_falls_back_when_prebuilt_manifest_conversion_fails(
    tmp_path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )
    repository_id = uuid4()
    manifest = RegistryVersionManifest.from_actions(
        [
            RegistryActionCreate(
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
                default_title="Prebuilt title",
                display_group=None,
                doc_url=None,
                author=None,
                deprecated=None,
                options=RegistryActionOptions(),
            )
        ]
    )
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    write_prebuilt_registry_manifest(artifact_dir=artifact_dir, manifest=manifest)

    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=repository_id,
        origin=DEFAULT_REGISTRY_ORIGIN,
        origin_type="builtin",
        target_version="1.2.3",
        storage_namespace="platform",
        validate_actions=True,
    )

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    artifact_result = _make_artifact_result(tmp_path)
    mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    fallback_actions = [
        RegistryActionCreate(
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
            default_title="Recovered title",
            display_group=None,
            doc_url=None,
            author=None,
            deprecated=None,
            options=RegistryActionOptions(),
        )
    ]
    discover_actions = mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(return_value=(fallback_actions, {})),
    )
    mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(
            return_value=(
                "s3://registry/platform/tarball-venvs/tracecat_registry/generated/"
                "site-packages.squashfs"
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

    result = await runner.run(request)

    assert result.actions == fallback_actions
    discover_actions.assert_awaited_once_with(
        repository_id=repository_id,
        origin=DEFAULT_REGISTRY_ORIGIN,
        commit_sha=None,
        validate=True,
        git_repo_package_name=None,
        organization_id=None,
    )


@pytest.mark.anyio
async def test_runner_uses_prebuilt_manifest_without_discovery(
    tmp_path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
        str(tmp_path),
    )
    repository_id = uuid4()
    manifest = RegistryVersionManifest.from_actions(
        [
            RegistryActionCreate(
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
                default_title="Prebuilt title",
                display_group=None,
                doc_url=None,
                author=None,
                deprecated=None,
                options=RegistryActionOptions(),
            )
        ]
    )
    artifact_dir = get_artifact_local_dir(
        root=tmp_path,
        organization_id="platform",
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version="1.2.3",
    )
    write_prebuilt_registry_manifest(artifact_dir=artifact_dir, manifest=manifest)

    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=repository_id,
        origin=DEFAULT_REGISTRY_ORIGIN,
        origin_type="builtin",
        target_version="1.2.3",
        storage_namespace="platform",
        validate_actions=True,
    )

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    artifact_result = _make_artifact_result(tmp_path)
    build_tarball_venv = mocker.patch.object(
        runner,
        "_build_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    discover_actions = mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(
            return_value=(
                "s3://registry/platform/tarball-venvs/tracecat_registry/generated/"
                "site-packages.squashfs"
            )
        ),
    )

    result = await runner.run(request)

    assert result.tarball_uri.endswith("/site-packages.squashfs")
    assert len(result.actions) == 1
    assert result.actions[0].default_title == "Prebuilt title"
    build_tarball_venv.assert_awaited_once()
    discover_actions.assert_not_awaited()
    upload_tarball.assert_awaited_once()
