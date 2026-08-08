"""Tests for SSH base functionality."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.git.utils import GitUrl
from tracecat.ssh import (
    SshEnv,
    add_host_to_known_hosts_sync,
    add_ssh_key_to_agent_sync,
    get_git_ssh_command,
    temporary_ssh_agent,
)


class TestGetGitSshCommand:
    """Test get_git_ssh_command function."""

    @pytest.mark.anyio
    async def test_get_git_ssh_command(self):
        """Test get_git_ssh_command returns proper SSH command."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)
        role = Role(
            type="service",
            service_id="tracecat-service",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
        )

        mock_service = AsyncMock()
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "ssh-private-key"
        mock_service.get_ssh_key.return_value = mock_secret

        expected_ssh_cmd = "ssh -i /path/to/key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

        with (
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.prepare_ssh_key_file", return_value=expected_ssh_cmd),
        ):
            result = await get_git_ssh_command(git_url, session=mock_session, role=role)

        assert result == expected_ssh_cmd
        mock_service.get_ssh_key.assert_called_once()

    @pytest.mark.anyio
    async def test_get_git_ssh_command_with_context_role(self):
        """Test get_git_ssh_command with context role."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)

        mock_service = AsyncMock()
        mock_secret = MagicMock()
        mock_service.get_ssh_key.return_value = mock_secret

        expected_ssh_cmd = "ssh -i /path/to/key -o IdentitiesOnly=yes"

        # Mock the ctx_role module instead of ctx_role.get
        mock_role = Role(
            type="service",
            service_id="tracecat-service",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
        )
        with (
            patch("tracecat.ssh.ctx_role") as mock_ctx_role,
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.prepare_ssh_key_file", return_value=expected_ssh_cmd),
        ):
            mock_ctx_role.get.return_value = mock_role

            result = await get_git_ssh_command(git_url, session=mock_session, role=None)

        assert result == expected_ssh_cmd
        mock_ctx_role.get.assert_called_once()


class TestAddHostToKnownHosts:
    """Tests for add_host_to_known_hosts_sync."""

    def test_add_host_with_custom_port(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        env = SshEnv(ssh_auth_sock="/tmp/sock", ssh_agent_pid="123")
        captured_cmd: list[list[str]] = []

        def fake_run(cmd, capture_output, text, env, check, timeout=None):  # noqa: ANN001
            captured_cmd.append(cmd)
            return SimpleNamespace(
                returncode=0,
                stdout="[gitlab.example.com]:2222 ssh-rsa AAAA\n",
                stderr="",
            )

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.setattr("subprocess.run", fake_run)

        add_host_to_known_hosts_sync("gitlab.example.com:2222", env)

        assert captured_cmd == [["ssh-keyscan", "-p", "2222", "gitlab.example.com"]]
        known_hosts = tmp_path / ".ssh" / "known_hosts"
        assert known_hosts.read_text() == "[gitlab.example.com]:2222 ssh-rsa AAAA\n"

    def test_add_host_skips_existing_entry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        env = SshEnv(ssh_auth_sock="/tmp/sock", ssh_agent_pid="123")
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        known_hosts = ssh_dir / "known_hosts"
        known_hosts.write_text("[gitlab.example.com]:2222 ssh-rsa AAAA\n")

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        def fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
            raise AssertionError("ssh-keyscan should not run for existing hosts")

        monkeypatch.setattr("subprocess.run", fake_run)

        add_host_to_known_hosts_sync("gitlab.example.com:2222", env)

    @pytest.mark.parametrize(
        ("url", "expected_host", "expected_port"),
        (
            ("gitlab.example.com:2222", "gitlab.example.com", "2222"),
            ("[gitlab.example.com]:2222", "gitlab.example.com", "2222"),
            ("gitlab.example.com", "gitlab.example.com", None),
            ("[2001:db8::1]:2222", "2001:db8::1", "2222"),
        ),
    )
    def test_split_host_port(
        self, url: str, expected_host: str, expected_port: str | None
    ) -> None:
        from tracecat.ssh import _split_host_port

        host, port = _split_host_port(url)

        assert host == expected_host
        assert port == expected_port


@pytest.mark.anyio
async def test_temporary_ssh_agent_uses_private_socket_and_exact_cleanup_env(
    tmp_path: Path,
    mocker,
) -> None:
    socket_dir = tmp_path / "agent"
    socket_path = socket_dir / "agent.sock"
    run = mocker.patch(
        "tracecat.ssh.subprocess.run",
        side_effect=[
            SimpleNamespace(
                returncode=0,
                stdout=(
                    f"SSH_AUTH_SOCK={socket_path}; export SSH_AUTH_SOCK;\n"
                    "SSH_AGENT_PID=4321; export SSH_AGENT_PID;\n"
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ],
    )

    async with temporary_ssh_agent(socket_dir=socket_dir) as env:
        assert env == SshEnv(
            ssh_auth_sock=str(socket_path),
            ssh_agent_pid="4321",
        )
        assert socket_dir.stat().st_mode & 0o777 == 0o700

    start_call, stop_call = run.call_args_list
    assert start_call.args[0] == ["ssh-agent", "-s", "-a", str(socket_path)]
    assert stop_call.args[0] == ["ssh-agent", "-k"]
    assert stop_call.kwargs["env"] == {
        "SSH_AUTH_SOCK": str(socket_path),
        "SSH_AGENT_PID": "4321",
    }


def test_add_ssh_key_applies_lifetime_and_destination_constraint(
    tmp_path: Path,
    mocker,
) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("git.example.test ssh-ed25519 synthetic\n")
    env = SshEnv(ssh_auth_sock="/tmp/agent.sock", ssh_agent_pid="123")
    validate_key = mocker.patch("tracecat.ssh.paramiko.Ed25519Key.from_private_key")
    run = mocker.patch(
        "tracecat.ssh.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    add_ssh_key_to_agent_sync(
        "fake-private-key",
        env,
        lifetime_seconds=150,
        destination="git@git.example.test",
        known_hosts_path=known_hosts,
    )

    validate_key.assert_called_once()
    run.assert_called_once_with(
        [
            "ssh-add",
            "-t",
            "150",
            "-H",
            str(known_hosts),
            "-h",
            "git@git.example.test",
            "-",
        ],
        input="fake-private-key\n",
        capture_output=True,
        text=True,
        env=env.to_dict(),
        check=False,
        timeout=30.0,
    )
