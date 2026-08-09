from __future__ import annotations

import json
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from tracecat.exceptions import RegistryError
from tracecat.registry.sync.artifact import RegistryArtifactBuildResult
from tracecat.registry.sync.sandbox import RegistrySyncSandbox
from tracecat.registry.sync.schemas import SyncResultError, SyncResultSuccess
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.types import SandboxConfig, SandboxResult
from tracecat.ssh import SshEnv


@pytest.mark.anyio
async def test_registry_install_runs_in_nsjail_with_private_cache(
    tmp_path: Path,
    mocker,
) -> None:
    package_path = tmp_path / "registry-source"
    package_path.mkdir()
    (package_path / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    (package_path / "example.py").write_text("VALUE = 1\n")
    (package_path / "link.py").symlink_to("example.py")

    output_dir = tmp_path / "output"
    squashfs_path = output_dir / "site-packages.squashfs"
    built_result = RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name=squashfs_path.name,
        content_hash="hash",
        artifact_size_bytes=1,
        site_packages_path=(output_dir / "sandbox-install" / "cache" / "site-packages"),
    )

    async def execute_install(
        job_dir: Path,
        cache_key: str,
        timeout_seconds: int,
    ) -> SandboxResult:
        assert cache_key == "0" * 16
        assert timeout_seconds == 123
        assert (job_dir / "source" / "link.py").is_symlink()
        assert json.loads((job_dir / "dependencies.json").read_text()) == [
            "/work/source"
        ]
        (job_dir / "cache" / "site-packages" / "example.py").write_text("VALUE = 1\n")
        return SandboxResult(success=True)

    executor = mocker.Mock(
        execute_install=mocker.AsyncMock(side_effect=execute_install)
    )
    executor_cls = mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=executor,
    )
    sandbox = RegistrySyncSandbox()
    package_site_packages = mocker.patch.object(
        sandbox,
        "package_site_packages",
        mocker.AsyncMock(return_value=built_result),
    )

    result = await sandbox.build_execution_artifact(
        package_path=package_path,
        output_dir=output_dir,
        timeout_seconds=123,
    )

    assert result == built_result
    executor_cls.assert_called_once_with(
        cache_dir=str(output_dir / "sandbox-install" / "sandbox-cache")
    )
    package_site_packages.assert_awaited_once_with(
        site_packages=output_dir / "sandbox-install" / "cache" / "site-packages",
        output_dir=output_dir,
        timeout_seconds=123,
    )


@pytest.mark.anyio
async def test_registry_clone_uses_scoped_agent_and_strict_host_keys(
    tmp_path: Path,
    mocker,
) -> None:
    hashed_host_keys = "|1|synthetic-salt|synthetic-hash ssh-ed25519 synthetic-key"
    resolved_sha = "a" * 40
    events: list[str] = []

    @asynccontextmanager
    async def fake_agent(*, socket_dir: Path):
        events.append("agent")
        socket_dir.mkdir(parents=True)
        socket_path = socket_dir / "agent.sock"
        socket_path.touch()
        yield SshEnv(ssh_auth_sock=str(socket_path), ssh_agent_pid="123")

    async def execute(
        job_dir: Path,
        sandbox_config: SandboxConfig,
        cache_key=None,
        script_name: str = "wrapper.py",
    ) -> SandboxResult:
        assert cache_key is None
        if script_name == "keyscan.py":
            events.append("keyscan")
            assert job_dir == tmp_path / "sync" / "sandbox-keyscan"
            assert sandbox_config.network_enabled is True
            assert sandbox_config.resources.timeout_seconds == 30
            assert sandbox_config.resources.cpu_seconds == 30
            assert sandbox_config.env_vars == {}
            assert sandbox_config.bind_mounts == []
            assert sandbox_config.python_path_dirs == []
            assert sandbox_config.action_gateway_socket is None
            assert json.loads((job_dir / "inputs.json").read_text()) == {
                "host": "git.example.test",
                "port": 2222,
            }
            keyscan_script = (job_dir / "keyscan.py").read_text()
            assert 'command = ["ssh-keyscan", "-H"]' in keyscan_script
            assert 'command.extend(["-p", str(inputs["port"])])' in keyscan_script
            assert "timeout=30.0" in keyscan_script
            assert "fake-private-key" not in repr(sandbox_config)
            return SandboxResult(success=True, output=hashed_host_keys)

        events.append("clone")
        assert events == ["keyscan", "agent", "clone"]
        assert script_name == "wrapper.py"
        assert sandbox_config.network_enabled is True
        assert sandbox_config.env_vars["SSH_AUTH_SOCK"] == (
            "/run/registry-agent/agent.sock"
        )
        assert "StrictHostKeyChecking=yes" in sandbox_config.env_vars["GIT_SSH_COMMAND"]
        assert "ForwardAgent=no" in sandbox_config.env_vars["GIT_SSH_COMMAND"]
        assert "fake-private-key" not in repr(sandbox_config)
        assert "GIT_CONFIG_NOSYSTEM" in sandbox_config.env_vars
        assert "GIT_CONFIG_GLOBAL" in sandbox_config.env_vars
        assert "core.hooksPath" in sandbox_config.env_vars.values()
        assert all(not mount.writable for mount in sandbox_config.bind_mounts)
        assert {mount.destination for mount in sandbox_config.bind_mounts} == {
            Path("/run/registry-agent"),
            Path("/run/registry-ssh/known_hosts"),
        }
        known_hosts_mount = next(
            mount
            for mount in sandbox_config.bind_mounts
            if mount.destination == Path("/run/registry-ssh/known_hosts")
        )
        assert known_hosts_mount.source == (
            tmp_path / "sync" / "registry-ssh" / "known_hosts"
        )
        assert known_hosts_mount.source.read_text() == f"{hashed_host_keys}\n"
        assert stat.S_IMODE(known_hosts_mount.source.stat().st_mode) == 0o600
        clone_inputs = json.loads((job_dir / "inputs.json").read_text())
        assert clone_inputs == {
            "clone_url": "ssh://git@git.example.test:2222/acme/registry.git",
            "commit_sha": resolved_sha,
        }
        script = (job_dir / "script.py").read_text()
        assert '"--no-checkout"' in script
        assert '"FETCH_HEAD"' in script
        assert '"--", "origin", commit_sha' in script
        (job_dir / "repo").mkdir()
        return SandboxResult(
            success=True,
            output={"commit_sha": resolved_sha},
        )

    add_key = mocker.patch(
        "tracecat.registry.sync.sandbox.add_ssh_key_to_agent",
        mocker.AsyncMock(),
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox.temporary_ssh_agent",
        side_effect=fake_agent,
    )
    executor_cls = mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=mocker.AsyncMock(side_effect=execute)),
    )

    clone_path, commit_sha = await RegistrySyncSandbox().clone_repository(
        git_url="git+ssh://git@git.example.test:2222/acme/registry.git",
        commit_sha=resolved_sha,
        ssh_key="fake-private-key",
        work_dir=tmp_path / "sync",
        timeout_seconds=90,
    )

    assert clone_path == tmp_path / "sync" / "sandbox-clone" / "work" / "repo"
    assert commit_sha == resolved_sha
    assert executor_cls.call_count == 2
    known_hosts = tmp_path / "sync" / "registry-ssh" / "known_hosts"
    add_key.assert_awaited_once_with(
        "fake-private-key",
        mocker.ANY,
        lifetime_seconds=120,
        destination="git@[git.example.test]:2222",
        known_hosts_path=known_hosts,
    )


@pytest.mark.anyio
async def test_registry_clone_stops_when_keyscan_times_out(
    tmp_path: Path,
    mocker,
) -> None:
    execute = mocker.AsyncMock(
        side_effect=SandboxTimeoutError("synthetic keyscan timeout")
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=execute),
    )
    temporary_agent = mocker.patch("tracecat.registry.sync.sandbox.temporary_ssh_agent")

    with pytest.raises(RegistryError, match="host key scan timed out"):
        await RegistrySyncSandbox().clone_repository(
            git_url="git+ssh://git@git.example.test/acme/registry.git",
            commit_sha="a" * 40,
            ssh_key="fake-private-key",
            work_dir=tmp_path / "sync",
            timeout_seconds=90,
        )

    execute.assert_awaited_once()
    temporary_agent.assert_not_called()


@pytest.mark.anyio
async def test_registry_clone_stops_when_keyscan_command_fails(
    tmp_path: Path,
    mocker,
) -> None:
    execute = mocker.AsyncMock(
        return_value=SandboxResult(
            success=False,
            error="RuntimeError: synthetic ssh-keyscan failure",
        )
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=execute),
    )
    temporary_agent = mocker.patch("tracecat.registry.sync.sandbox.temporary_ssh_agent")

    with pytest.raises(RegistryError, match="host key scan failed"):
        await RegistrySyncSandbox().clone_repository(
            git_url="git+ssh://git@git.example.test/acme/registry.git",
            commit_sha="a" * 40,
            ssh_key="fake-private-key",
            work_dir=tmp_path / "sync",
            timeout_seconds=90,
        )

    execute.assert_awaited_once()
    temporary_agent.assert_not_called()


@pytest.mark.anyio
async def test_registry_clone_stops_when_keyscan_returns_empty_output(
    tmp_path: Path,
    mocker,
) -> None:
    execute = mocker.AsyncMock(return_value=SandboxResult(success=True, output="\n"))
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=execute),
    )
    temporary_agent = mocker.patch("tracecat.registry.sync.sandbox.temporary_ssh_agent")

    with pytest.raises(RegistryError, match="returned no host keys"):
        await RegistrySyncSandbox().clone_repository(
            git_url="git+ssh://git@git.example.test/acme/registry.git",
            commit_sha="a" * 40,
            ssh_key="fake-private-key",
            work_dir=tmp_path / "sync",
            timeout_seconds=90,
        )

    execute.assert_awaited_once()
    temporary_agent.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("git_url", "error"),
    [
        (
            "git+ssh://git@-oProxyCommand=evil/acme/registry.git",
            "Git host is invalid",
        ),
        (
            "git+ssh://-oProxyCommand=evil@git.example.test/acme/registry.git",
            "Git SSH user is invalid",
        ),
        (
            "git+ssh://git@git.example.test:70000/acme/registry.git",
            "Git port is invalid",
        ),
    ],
)
async def test_registry_clone_rejects_invalid_ssh_target_before_keyscan(
    tmp_path: Path,
    mocker,
    git_url: str,
    error: str,
) -> None:
    executor_cls = mocker.patch("tracecat.registry.sync.sandbox.NsjailExecutor")
    temporary_agent = mocker.patch("tracecat.registry.sync.sandbox.temporary_ssh_agent")

    with pytest.raises(RegistryError, match=error):
        await RegistrySyncSandbox().clone_repository(
            git_url=git_url,
            commit_sha="a" * 40,
            ssh_key="fake-private-key",
            work_dir=tmp_path / "sync",
            timeout_seconds=90,
        )

    executor_cls.assert_not_called()
    temporary_agent.assert_not_called()


@pytest.mark.anyio
async def test_registry_packaging_runs_in_fresh_no_network_jail(
    tmp_path: Path,
    mocker,
) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "untrusted.py").write_text("VALUE = 1\n")
    output_dir = tmp_path / "output"

    async def execute(
        job_dir: Path,
        sandbox_config: SandboxConfig,
        cache_key=None,
        script_name: str = "wrapper.py",
    ) -> SandboxResult:
        assert cache_key is None
        assert script_name == "wrapper.py"
        assert sandbox_config.network_enabled is False
        assert sandbox_config.env_vars == {}
        assert sandbox_config.bind_mounts == [
            mocker.ANY,
        ]
        mount = sandbox_config.bind_mounts[0]
        assert mount.source == site_packages
        assert mount.destination == Path("/input/site-packages")
        assert mount.writable is False
        (job_dir / "site-packages.squashfs").write_bytes(b"squashfs")
        return SandboxResult(success=True)

    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=mocker.AsyncMock(side_effect=execute)),
    )

    result = await RegistrySyncSandbox().package_site_packages(
        site_packages=site_packages,
        output_dir=output_dir,
        timeout_seconds=60,
    )

    assert result.squashfs_path.read_bytes() == b"squashfs"
    assert result.site_packages_path == site_packages
    assert len(result.content_hash) == 64


@pytest.mark.anyio
async def test_registry_discovery_runs_without_network_or_worker_environment(
    tmp_path: Path,
    mocker,
) -> None:
    site_packages = tmp_path / "sandbox-install" / "cache" / "site-packages"
    site_packages.mkdir(parents=True)
    host_site_packages = tmp_path / "host-site-packages"
    host_site_packages.mkdir()
    trusted_tracecat = tmp_path / "trusted" / "tracecat"
    trusted_registry = tmp_path / "trusted" / "tracecat_registry"
    trusted_tracecat.mkdir(parents=True)
    trusted_registry.mkdir()
    (trusted_tracecat / "__init__.py").write_text("")
    (trusted_registry / "__init__.py").write_text("")

    async def execute(
        job_dir: Path,
        sandbox_config,
        cache_key=None,
        script_name: str = "wrapper.py",
    ) -> SandboxResult:
        assert cache_key is None
        assert script_name == "wrapper.py"
        assert sandbox_config.network_enabled is False
        assert sandbox_config.env_vars == {}
        assert job_dir.parent.name.startswith("tracecat_registry_discovery_")
        assert job_dir.parent != site_packages.parent.parent / "sandbox-discovery"
        assert sandbox_config.python_path_dirs == [
            job_dir.parent / "trusted" / "0",
            site_packages,
            host_site_packages,
            job_dir.parent / "trusted" / "1",
        ]
        assert (job_dir.parent / "trusted" / "0" / "tracecat" / "__init__.py").exists()
        assert (
            job_dir.parent / "trusted" / "1" / "tracecat_registry" / "__init__.py"
        ).exists()
        inputs = json.loads((job_dir / "inputs.json").read_text())
        assert set(inputs) == {
            "origin",
            "package_name",
            "repository_id",
            "commit_sha",
            "validate",
            "organization_id",
        }
        return SandboxResult(
            success=True,
            output=SyncResultSuccess(actions=[]).model_dump(mode="json"),
        )

    executor = mocker.Mock(execute=mocker.AsyncMock(side_effect=execute))
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=executor,
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._host_site_packages_paths",
        return_value=[host_site_packages],
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._trusted_runtime_package_paths",
        return_value=[trusted_tracecat, trusted_registry],
    )

    result = await RegistrySyncSandbox().discover_actions(
        site_packages=site_packages,
        origin="git+ssh://git@example.test/acme/custom-registry.git",
        package_name="custom_registry",
        repository_id=uuid4(),
        commit_sha="abc123",
        validate=True,
        organization_id=uuid4(),
        timeout_seconds=45,
    )

    assert result.actions == []


@pytest.mark.anyio
async def test_registry_discovery_ignores_installer_planted_symlinks(
    tmp_path: Path,
    mocker,
) -> None:
    site_packages = tmp_path / "sandbox-install" / "cache" / "site-packages"
    site_packages.mkdir(parents=True)
    old_job_dir = site_packages.parent.parent / "sandbox-discovery" / "work"
    old_job_dir.mkdir(parents=True)
    executor_owned_file = tmp_path / "executor-owned.py"
    executor_owned_file.write_text("SAFE\n")
    (old_job_dir / "script.py").symlink_to(executor_owned_file)

    trusted_tracecat = tmp_path / "trusted" / "tracecat"
    trusted_registry = tmp_path / "trusted" / "tracecat_registry"
    trusted_tracecat.mkdir(parents=True)
    trusted_registry.mkdir()
    discovery_job_dirs: list[Path] = []

    async def execute(
        job_dir: Path,
        _sandbox_config,
        cache_key=None,
        script_name: str = "wrapper.py",
    ) -> SandboxResult:
        assert cache_key is None
        assert script_name == "wrapper.py"
        assert job_dir != old_job_dir
        assert not (job_dir / "script.py").is_symlink()
        discovery_job_dirs.append(job_dir)
        return SandboxResult(
            success=True,
            output=SyncResultSuccess(actions=[]).model_dump(mode="json"),
        )

    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=mocker.Mock(execute=mocker.AsyncMock(side_effect=execute)),
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._host_site_packages_paths",
        return_value=[],
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._trusted_runtime_package_paths",
        return_value=[trusted_tracecat, trusted_registry],
    )

    await RegistrySyncSandbox().discover_actions(
        site_packages=site_packages,
        origin="tracecat_registry",
        package_name="tracecat_registry",
        repository_id=uuid4(),
        commit_sha=None,
        validate=True,
        organization_id=None,
        timeout_seconds=45,
    )

    assert executor_owned_file.read_text() == "SAFE\n"
    assert (old_job_dir / "script.py").is_symlink()
    assert len(discovery_job_dirs) == 1
    assert not discovery_job_dirs[0].exists()


@pytest.mark.anyio
async def test_registry_discovery_preserves_structured_registry_errors(
    tmp_path: Path,
    mocker,
) -> None:
    site_packages = tmp_path / "sandbox-install" / "cache" / "site-packages"
    site_packages.mkdir(parents=True)
    trusted_tracecat = tmp_path / "trusted" / "tracecat"
    trusted_registry = tmp_path / "trusted" / "tracecat_registry"
    trusted_tracecat.mkdir(parents=True)
    trusted_registry.mkdir()
    executor = mocker.Mock(
        execute=mocker.AsyncMock(
            return_value=SandboxResult(
                success=True,
                output=SyncResultError(
                    error="Failed to load template action from invalid.yml"
                ).model_dump(mode="json"),
            )
        )
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox.NsjailExecutor",
        return_value=executor,
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._host_site_packages_paths",
        return_value=[],
    )
    mocker.patch(
        "tracecat.registry.sync.sandbox._trusted_runtime_package_paths",
        return_value=[trusted_tracecat, trusted_registry],
    )

    with pytest.raises(RegistryError, match="Failed to load template action"):
        await RegistrySyncSandbox().discover_actions(
            site_packages=site_packages,
            origin="tracecat_registry",
            package_name="tracecat_registry",
            repository_id=uuid4(),
            commit_sha=None,
            validate=True,
            organization_id=None,
            timeout_seconds=45,
        )
