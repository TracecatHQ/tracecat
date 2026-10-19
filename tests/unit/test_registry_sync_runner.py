from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import RegistryError, RegistrySyncContentError
from tracecat.executor.enums import ExecutorBackendType
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionOptions,
    RegistryActionUDFImpl,
    RegistryActionValidationErrorInfo,
)
from tracecat.registry.artifact_keys import get_artifact_local_dir
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.sync.artifact import (
    RegistryArtifactBuildError,
    RegistryArtifactBuildResult,
)
from tracecat.registry.sync.prebuilt import write_prebuilt_registry_manifest
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    ArtifactBuildError,
    GitCloneError,
    PackageInstallError,
    RegistrySyncRunner,
    RegistrySyncRunnerError,
    RegistrySyncValidationError,
    _SandboxedBackend,
    _UnsandboxedBackend,
)
from tracecat.registry.sync.sandbox import RegistrySyncSandbox
from tracecat.registry.sync.schemas import (
    RegistrySyncRequest,
    SyncErrorCode,
    SyncResultSuccess,
)
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.sandbox.types import SandboxResult


def _make_artifact_result(tmp_path: Path) -> RegistryArtifactBuildResult:
    squashfs_path = tmp_path / "site-packages.squashfs"
    squashfs_path.write_bytes(b"squashfs")
    return RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name="site-packages.squashfs",
        content_hash="hash",
        artifact_size_bytes=len(b"squashfs"),
    )


def _make_action(repository_id: UUID, default_title: str) -> RegistryActionCreate:
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


def _make_sandboxed_runner(
    mocker,
    sandbox,
    *,
    install_timeout: int | None = None,
    discover_timeout: int | None = None,
    clone_timeout: int | None = None,
) -> RegistrySyncRunner:
    mocker.patch.object(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    runner = RegistrySyncRunner(
        install_timeout=install_timeout,
        discover_timeout=discover_timeout,
        clone_timeout=clone_timeout,
    )
    runner._backend = _SandboxedBackend(sandbox)
    return runner


def test_write_prebuilt_registry_manifest_is_compact(tmp_path: Path) -> None:
    manifest = RegistryVersionManifest.from_actions(
        [_make_action(uuid4(), "Prebuilt title")]
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


def test_resolve_package_name_ignores_git_ref_suffix(tmp_path: Path) -> None:
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git@feature/ref",
        origin_type="git",
        git_url="git+ssh://git@git.example.test/acme/registry.git@feature/ref",
        organization_id=uuid4(),
    )

    assert RegistrySyncRunner._resolve_package_name(request, tmp_path) == "registry"


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
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    discover_actions = mocker.patch.object(
        runner,
        "_discover_unsandboxed_actions",
        mocker.AsyncMock(return_value=SyncResultSuccess(actions=[])),
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
    discover_actions.assert_awaited_once_with(request, "resolved-sha")
    upload_tarball.assert_awaited_once()
    assert result.commit_sha == "resolved-sha"


@pytest.mark.anyio
async def test_runner_routes_git_clone_through_nsjail_when_available(
    tmp_path: Path,
    mocker,
) -> None:
    resolved_sha = "b" * 40
    requested_sha = "a" * 40
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type="git",
        git_url="git+ssh://git@git.example.test/acme/registry.git",
        commit_sha=requested_sha,
        organization_id=uuid4(),
    )
    artifact_result = _make_artifact_result(tmp_path)
    installed_site_packages = tmp_path / "site-packages"
    sandbox = mocker.Mock(
        clone_repository=mocker.AsyncMock(
            return_value=(tmp_path / "sandbox-clone" / "repo", resolved_sha)
        ),
        install_package=mocker.AsyncMock(return_value=installed_site_packages),
        package_site_packages=mocker.AsyncMock(return_value=artifact_result),
    )
    runner = _make_sandboxed_runner(mocker, sandbox, clone_timeout=75)
    fetch_key = mocker.patch.object(
        runner,
        "_fetch_registry_ssh_key",
        mocker.AsyncMock(return_value="fake-private-key"),
    )
    unsandboxed_clone = mocker.patch.object(
        runner,
        "_clone_repository",
        mocker.AsyncMock(),
    )
    build_execution_artifact = mocker.patch.object(
        runner,
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    discover = mocker.patch.object(
        runner,
        "_discover_sandboxed_actions",
        mocker.AsyncMock(return_value=SyncResultSuccess(actions=[])),
    )
    mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(return_value="s3://registry/site-packages.squashfs"),
    )

    result = await runner.run(request)

    fetch_key.assert_awaited_once_with(request.organization_id)
    sandbox.clone_repository.assert_awaited_once_with(
        git_url=request.git_url,
        commit_sha=requested_sha,
        ssh_key="fake-private-key",
        work_dir=mocker.ANY,
        timeout_seconds=75,
    )
    unsandboxed_clone.assert_not_awaited()
    build_execution_artifact.assert_not_awaited()
    sandbox.install_package.assert_awaited_once_with(
        package_path=tmp_path / "sandbox-clone" / "repo",
        output_dir=mocker.ANY,
        timeout_seconds=runner.install_timeout,
    )
    discover.assert_awaited_once()
    discover_call = discover.await_args.kwargs
    assert discover_call["request"] is request
    assert discover_call["resolved"].path == tmp_path / "sandbox-clone" / "repo"
    assert discover_call["resolved"].commit_sha == resolved_sha
    assert discover_call["installed_site_packages"] == installed_site_packages
    assert discover_call["sandbox"] is sandbox
    sandbox.package_site_packages.assert_awaited_once_with(
        site_packages=installed_site_packages,
        output_dir=mocker.ANY,
        timeout_seconds=runner.install_timeout,
    )
    assert result.commit_sha == resolved_sha


@pytest.mark.anyio
async def test_runner_rejects_local_registry_when_disabled(
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", False)
    runner = RegistrySyncRunner()
    build_execution_artifact = mocker.patch.object(
        runner,
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin=DEFAULT_LOCAL_REGISTRY_ORIGIN,
        origin_type="local",
    )

    with pytest.raises(
        RegistrySyncRunnerError, match="Local repository is not enabled"
    ):
        await runner.run(request)

    build_execution_artifact.assert_not_awaited()


@pytest.mark.parametrize(
    ("backend_type", "expected_backend"),
    [
        (ExecutorBackendType.NSJAIL, _SandboxedBackend),
        (ExecutorBackendType.DIRECT, _UnsandboxedBackend),
    ],
)
def test_runner_selects_backend_from_executor_config(
    mocker,
    backend_type: ExecutorBackendType,
    expected_backend: type[_SandboxedBackend] | type[_UnsandboxedBackend],
) -> None:
    mocker.patch.object(config, "TRACECAT__EXECUTOR_BACKEND", backend_type)

    runner = RegistrySyncRunner()

    assert isinstance(runner._backend, expected_backend)


@pytest.mark.anyio
async def test_runner_wraps_sandbox_clone_failures(mocker) -> None:
    sandbox = mocker.Mock(
        clone_repository=mocker.AsyncMock(
            side_effect=RegistryError("host key verification failed")
        )
    )
    runner = _make_sandboxed_runner(mocker, sandbox)
    mocker.patch.object(
        runner,
        "_fetch_registry_ssh_key",
        mocker.AsyncMock(return_value="fake-private-key"),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type="git",
        git_url="git+ssh://git@git.example.test/acme/registry.git",
        commit_sha="a" * 40,
        organization_id=uuid4(),
    )

    with pytest.raises(GitCloneError, match="host key verification failed"):
        await runner.run(request)


@pytest.mark.anyio
async def test_runner_wraps_sandbox_install_failures(
    tmp_path: Path,
    mocker,
) -> None:
    package_path = tmp_path / "registry"
    package_path.mkdir()
    (package_path / "pyproject.toml").write_text("[project]\nname='registry'\n")
    mocker.patch("tracecat.registry.sync.sandbox.sandbox_cgroup", return_value=None)
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor.execute_install",
        mocker.AsyncMock(
            return_value=SandboxResult(
                success=False, error="No solution found when resolving dependencies"
            )
        ),
    )
    runner = _make_sandboxed_runner(mocker, RegistrySyncSandbox())
    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=package_path),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(
        PackageInstallError,
        match="installation failed: No solution found when resolving dependencies",
    ) as exc_info:
        await runner.run(request)
    assert isinstance(exc_info.value.__cause__, RegistryArtifactBuildError)


@pytest.mark.anyio
async def test_runner_wraps_sandbox_packaging_failures(
    tmp_path: Path,
    mocker,
) -> None:
    sandbox = mocker.Mock(
        install_package=mocker.AsyncMock(return_value=tmp_path / "site-packages"),
        package_site_packages=mocker.AsyncMock(
            side_effect=RegistryArtifactBuildError(
                "Sandboxed registry packaging failed: mksquashfs exited 1"
            )
        ),
    )
    runner = _make_sandboxed_runner(mocker, sandbox)
    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_discover_sandboxed_actions",
        mocker.AsyncMock(return_value=SyncResultSuccess(actions=[])),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(ArtifactBuildError, match="mksquashfs exited 1"):
        await runner.run(request)


@pytest.mark.anyio
async def test_runner_wraps_unsandboxed_artifact_build_failures(
    tmp_path: Path,
    mocker,
) -> None:
    mocker.patch.object(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.DIRECT
    )
    mocker.patch(
        "tracecat.registry.sync.runner.build_artifact_from_path",
        mocker.AsyncMock(
            side_effect=RegistryArtifactBuildError(
                "Failed to install package: resolver error"
            )
        ),
    )
    runner = RegistrySyncRunner()
    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(ArtifactBuildError, match="resolver error"):
        await runner.run(request)


@pytest.mark.anyio
async def test_runner_decreases_privileges_across_sandbox_phases(
    tmp_path: Path,
    mocker,
) -> None:
    events: list[str] = []
    installed_site_packages = tmp_path / "site-packages"
    artifact_result = _make_artifact_result(tmp_path)

    async def install_package(**_kwargs) -> Path:
        events.append("install")
        return installed_site_packages

    async def discover_actions(**_kwargs) -> SyncResultSuccess:
        events.append("discover")
        return SyncResultSuccess(actions=[])

    async def package_site_packages(**_kwargs) -> RegistryArtifactBuildResult:
        events.append("package")
        return artifact_result

    async def upload_squashfs(**_kwargs) -> str:
        events.append("upload")
        return "s3://registry/site-packages.squashfs"

    sandbox = mocker.Mock(
        install_package=mocker.AsyncMock(side_effect=install_package),
        discover_actions=mocker.AsyncMock(side_effect=discover_actions),
        package_site_packages=mocker.AsyncMock(side_effect=package_site_packages),
    )
    runner = _make_sandboxed_runner(mocker, sandbox)
    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_upload_squashfs",
        mocker.AsyncMock(side_effect=upload_squashfs),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    await runner.run(request)

    assert events == ["install", "discover", "package", "upload"]


@pytest.mark.anyio
async def test_runner_does_not_package_invalid_sandbox_discovery(
    tmp_path: Path,
    mocker,
) -> None:
    installed_site_packages = tmp_path / "site-packages"
    validation_error = RegistryActionValidationErrorInfo(
        type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
        details=["Synthetic validation failure"],
        is_template=False,
        loc_primary="tools.example.action",
        loc_secondary=None,
    )
    package_site_packages = mocker.AsyncMock()
    sandbox = mocker.Mock(
        install_package=mocker.AsyncMock(return_value=installed_site_packages),
        discover_actions=mocker.AsyncMock(
            return_value=SyncResultSuccess(
                actions=[],
                validation_errors={"tools.example.action": [validation_error]},
            )
        ),
        package_site_packages=package_site_packages,
    )
    runner = _make_sandboxed_runner(mocker, sandbox)
    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
        validate_actions=True,
    )

    with pytest.raises(RegistrySyncValidationError):
        await runner.run(request)

    package_site_packages.assert_not_awaited()


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
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    mocker.patch.object(
        runner,
        "_discover_unsandboxed_actions",
        mocker.AsyncMock(
            return_value=SyncResultSuccess(
                actions=[],
                validation_errors={
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
            side_effect=RegistrySyncContentError(
                "Failed to load template action from "
                "/custom_actions/example_template.yml: invalid annotation",
                code=SyncErrorCode.TEMPLATE_LOAD_FAILED,
            )
        ),
    )

    with pytest.raises(ActionDiscoveryError) as exc_info:
        await runner._discover_unsandboxed_actions(
            RegistrySyncRequest(
                repository_id=uuid4(),
                origin="tracecat_registry",
                origin_type="builtin",
            ),
            None,
        )

    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_discover_actions_marks_missing_package_non_retryable(mocker) -> None:
    runner = RegistrySyncRunner()
    mocker.patch(
        "tracecat.registry.sync.runner.fetch_actions_from_subprocess",
        mocker.AsyncMock(
            side_effect=RegistrySyncContentError(
                "No module named 'internal-registry'",
                code=SyncErrorCode.PACKAGE_NOT_FOUND,
            )
        ),
    )

    with pytest.raises(ActionDiscoveryError) as exc_info:
        await runner._discover_unsandboxed_actions(
            RegistrySyncRequest(
                repository_id=uuid4(),
                origin="tracecat_registry",
                origin_type="builtin",
            ),
            None,
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
        await runner._discover_unsandboxed_actions(
            RegistrySyncRequest(
                repository_id=uuid4(),
                origin="tracecat_registry",
                origin_type="builtin",
            ),
            None,
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
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(return_value=artifact_result),
    )
    mocker.patch.object(
        runner,
        "_discover_unsandboxed_actions",
        mocker.AsyncMock(return_value=SyncResultSuccess(actions=[])),
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
@pytest.mark.parametrize("prebuilt", ["valid", "invalid_json", "conversion_error"])
async def test_runner_uses_prebuilt_manifest_or_falls_back_to_discovery(
    tmp_path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    prebuilt: str,
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
    if prebuilt == "invalid_json":
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "manifest.json").write_text("{invalid")
    else:
        manifest = RegistryVersionManifest.from_actions(
            [_make_action(repository_id, "Prebuilt title")]
        )
        write_prebuilt_registry_manifest(artifact_dir=artifact_dir, manifest=manifest)
    if prebuilt == "conversion_error":

        def raise_conversion_error(*_args, **_kwargs) -> list[RegistryActionCreate]:
            raise ValueError("bad prebuilt action payload")

        monkeypatch.setattr(
            RegistryVersionManifest,
            "to_action_creates",
            raise_conversion_error,
        )

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
    build_artifact = mocker.patch.object(
        runner,
        "_build_unsandboxed_execution_artifact",
        mocker.AsyncMock(return_value=_make_artifact_result(tmp_path)),
    )
    fallback_actions = [_make_action(repository_id, "Recovered title")]
    discover_actions = mocker.patch.object(
        runner,
        "_discover_unsandboxed_actions",
        mocker.AsyncMock(return_value=SyncResultSuccess(actions=fallback_actions)),
    )
    upload_squashfs = mocker.patch.object(
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
    build_artifact.assert_awaited_once()
    upload_squashfs.assert_awaited_once()
    if prebuilt == "valid":
        assert [action.default_title for action in result.actions] == ["Prebuilt title"]
        discover_actions.assert_not_awaited()
    else:
        assert result.actions == fallback_actions
        discover_actions.assert_awaited_once_with(request, None)
