from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr

from tracecat.auth.types import Role
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.runner import (
    RegistrySyncRunner,
    RegistrySyncValidationError,
)
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.tarball import TarballVenvBuildResult


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

    tarball_path = tmp_path / "site-packages.tar.gz"
    tarball_path.write_bytes(b"tarball")

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
        "_build_tarball_venv",
        mocker.AsyncMock(
            return_value=TarballVenvBuildResult(
                tarball_path=tarball_path,
                tarball_name="site-packages.tar.gz",
                content_hash="hash",
                compressed_size_bytes=7,
            )
        ),
    )
    discover_actions = mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(return_value=([], {})),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_tarball",
        mocker.AsyncMock(return_value="s3://registry/tarball.tgz"),
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

    tarball_path = tmp_path / "site-packages.tar.gz"
    tarball_path.write_bytes(b"tarball")

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_build_tarball_venv",
        mocker.AsyncMock(
            return_value=TarballVenvBuildResult(
                tarball_path=tarball_path,
                tarball_name="site-packages.tar.gz",
                content_hash="hash",
                compressed_size_bytes=7,
            )
        ),
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
        "_upload_tarball",
        mocker.AsyncMock(return_value="s3://registry/tarball.tgz"),
    )

    with pytest.raises(
        RegistrySyncValidationError,
        match="Registry sync validation failed: 1 validation error",
    ):
        await runner.run(request)

    upload_tarball.assert_not_awaited()
