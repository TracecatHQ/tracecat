"""Tests for SSH base functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.git.utils import GitUrl
from tracecat.ssh import get_git_ssh_command, git_env_context
from tracecat.types.auth import Role


class TestGetGitSshCommand:
    """Test get_git_ssh_command function."""

    @pytest.mark.anyio
    async def test_get_git_ssh_command(self):
        """Test get_git_ssh_command returns proper SSH command."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)
        role = Role(type="service", service_id="tracecat-service")

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
        mock_role = Role(type="service", service_id="tracecat-service")
        with (
            patch("tracecat.ssh.ctx_role") as mock_ctx_role,
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.prepare_ssh_key_file", return_value=expected_ssh_cmd),
        ):
            mock_ctx_role.get.return_value = mock_role

            result = await get_git_ssh_command(git_url, session=mock_session, role=None)

        assert result == expected_ssh_cmd
        mock_ctx_role.get.assert_called_once()


class TestGitEnvContext:
    """Test git_env_context async context manager."""

    @pytest.mark.anyio
    async def test_git_env_context_yields_environment(self):
        """Test git_env_context yields proper environment variables."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)
        role = Role(type="service", service_id="tracecat-service")

        mock_service = AsyncMock()
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "ssh-private-key"
        mock_service.get_ssh_key.return_value = mock_secret

        mock_ssh_env = MagicMock()
        mock_ssh_env.to_dict.return_value = {
            "SSH_AUTH_SOCK": "/tmp/ssh-auth-sock",
            "SSH_AGENT_PID": "12345",
        }

        expected_git_ssh_cmd = "ssh -i /path/to/key -o IdentitiesOnly=yes"

        # Mock all the SSH functions
        with (
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.temporary_ssh_agent") as mock_ssh_agent,
            patch("tracecat.ssh.add_ssh_key_to_agent") as mock_add_key,
            patch("tracecat.ssh.add_host_to_known_hosts") as mock_add_host,
            patch(
                "tracecat.ssh.prepare_ssh_key_file", return_value=expected_git_ssh_cmd
            ),
        ):
            # Set up the context manager mock
            mock_ssh_agent.return_value.__aenter__.return_value = mock_ssh_env
            mock_ssh_agent.return_value.__aexit__.return_value = None

            async with git_env_context(
                git_url=git_url, session=mock_session, role=role
            ) as env_dict:
                assert env_dict["SSH_AUTH_SOCK"] == "/tmp/ssh-auth-sock"
                assert env_dict["SSH_AGENT_PID"] == "12345"
                assert env_dict["GIT_SSH_COMMAND"] == expected_git_ssh_cmd

            # Verify all the setup calls were made
            mock_add_key.assert_called_once_with("ssh-private-key", env=mock_ssh_env)
            mock_add_host.assert_called_once_with("github.com", env=mock_ssh_env)

    @pytest.mark.anyio
    async def test_git_env_context_cleanup(self):
        """Test git_env_context properly cleans up SSH agent."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)
        role = Role(type="service", service_id="tracecat-service")

        mock_service = AsyncMock()
        mock_secret = MagicMock()
        mock_service.get_ssh_key.return_value = mock_secret

        mock_ssh_env = MagicMock()
        mock_ssh_env.to_dict.return_value = {
            "SSH_AUTH_SOCK": "/tmp/sock",
            "SSH_AGENT_PID": "123",
        }

        # Mock the context manager to raise an exception to test cleanup
        with (
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.temporary_ssh_agent") as mock_ssh_agent,
            patch("tracecat.ssh.add_ssh_key_to_agent"),
            patch("tracecat.ssh.add_host_to_known_hosts"),
            patch("tracecat.ssh.prepare_ssh_key_file"),
        ):
            # Mock the async context manager
            cm = mock_ssh_agent.return_value
            cm.__aenter__ = AsyncMock(return_value=mock_ssh_env)
            cm.__aexit__ = AsyncMock(return_value=None)

            async with git_env_context(
                git_url=git_url, session=mock_session, role=role
            ):
                pass  # Context should work normally

            # Verify cleanup was called
            cm.__aexit__.assert_called_once()

    @pytest.mark.anyio
    async def test_git_env_context_with_context_role(self):
        """Test git_env_context with context role."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        mock_session = MagicMock(spec=AsyncSession)

        mock_service = AsyncMock()
        mock_secret = MagicMock()
        mock_service.get_ssh_key.return_value = mock_secret

        mock_ssh_env = MagicMock()
        mock_ssh_env.to_dict.return_value = {
            "SSH_AUTH_SOCK": "/tmp/sock",
            "SSH_AGENT_PID": "123",
        }

        # Mock the ctx_role module instead of ctx_role.get
        mock_role = Role(type="service", service_id="tracecat-service")
        with (
            patch("tracecat.ssh.ctx_role") as mock_ctx_role,
            patch("tracecat.ssh.SecretsService", return_value=mock_service),
            patch("tracecat.ssh.temporary_ssh_agent") as mock_ssh_agent,
            patch("tracecat.ssh.add_ssh_key_to_agent"),
            patch("tracecat.ssh.add_host_to_known_hosts"),
            patch("tracecat.ssh.prepare_ssh_key_file"),
        ):
            mock_ctx_role.get.return_value = mock_role

            # Mock the async context manager
            cm = mock_ssh_agent.return_value
            cm.__aenter__ = AsyncMock(return_value=mock_ssh_env)
            cm.__aexit__ = AsyncMock(return_value=None)

            async with git_env_context(
                git_url=git_url, session=mock_session, role=None
            ) as env_dict:
                assert isinstance(env_dict, dict)

            mock_ctx_role.get.assert_called_once()
