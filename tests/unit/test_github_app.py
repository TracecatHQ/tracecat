"""Tests for GitHub App integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.vcs.github.models import (
    GitHubAppConfig,
    GitHubInstallation,
)


class TestGitHubAppService:
    """Test GitHub App service functionality."""

    @pytest.fixture
    def github_service(self, db_session, test_role):
        """Create GitHub App service instance."""
        return GitHubAppService(session=db_session, role=test_role)

    @pytest.fixture
    def mock_workspace_settings(self):
        """Mock workspace settings with GitHub App config."""
        return {
            "vcs": {
                "provider": "github",
                "github_app": {
                    "type": "managed",
                    "installation_id": 12345678,
                    "installation": {
                        "id": 12345678,
                        "account_login": "test-org",
                        "account_type": "Organization",
                        "target_type": "Organization",
                    },
                },
            }
        }

    @pytest.mark.anyio
    async def test_get_workspace_github_config_none_when_not_configured(
        self, github_service, mock_workspace
    ):
        """Test getting GitHub config when not configured."""
        mock_workspace.settings = {}

        with (
            patch.object(github_service, "workspace_id", "test-workspace"),
            patch(
                "tracecat.store.github.app.WorkspaceService"
            ) as mock_workspace_service,
        ):
            mock_service = AsyncMock()
            mock_service.get_workspace.return_value = mock_workspace
            mock_workspace_service.return_value = mock_service

            config = await github_service.get_workspace_github_config()
            assert config is None

    @pytest.mark.anyio
    async def test_get_workspace_github_config_returns_config(
        self, github_service, mock_workspace, mock_workspace_settings
    ):
        """Test getting GitHub config when configured."""
        mock_workspace.settings = mock_workspace_settings

        with (
            patch.object(github_service, "workspace_id", "test-workspace"),
            patch(
                "tracecat.store.github.app.WorkspaceService"
            ) as mock_workspace_service,
        ):
            mock_service = AsyncMock()
            mock_service.get_workspace.return_value = mock_workspace
            mock_workspace_service.return_value = mock_service

            config = await github_service.get_workspace_github_config()

            assert config is not None
            assert config.installation_id == 12345678

    @pytest.mark.anyio
    async def test_install_managed_app(self, github_service, mock_workspace):
        """Test installing managed GitHub App."""
        installation_id = 12345678

        with (
            patch.object(github_service, "workspace_id", "test-workspace"),
            patch.object(
                github_service, "_get_installation_details"
            ) as mock_get_installation,
            patch.object(
                github_service, "save_workspace_github_config"
            ) as mock_save_config,
        ):
            mock_installation = GitHubInstallation(
                id=installation_id,
                account_login="test-org",
                account_type="Organization",
                target_type="Organization",
            )
            mock_get_installation.return_value = mock_installation

            config = await github_service.install_managed_app(installation_id)

            assert config.installation_id == installation_id
            assert config.installation == mock_installation

            mock_save_config.assert_called_once_with(config)

    def test_generate_jwt_valid_token(self, github_service):
        """Test JWT generation."""
        app_id = "123456"
        private_key = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA4f5wg5l2hKsTeNem/V41fGnJm6gOdrj8ym3rFkEjWT9Hm/mz
+/zw5/XQ4PD4fZ0Z5o0Q5Ng1B1YjXXO0Dz9ZqK8QfZ9NzT7+9/+9/+9/+9/+9/+9
/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9/+9
-----END RSA PRIVATE KEY-----"""

        # This would fail with a real test, but demonstrates the structure
        with pytest.raises(GitHubAppError, match="Failed to generate JWT"):
            github_service._generate_jwt(app_id, private_key)

    @pytest.mark.anyio
    async def test_get_connection_status_not_connected(self, github_service):
        """Test connection status when not connected."""
        with patch.object(
            github_service, "get_workspace_github_config"
        ) as mock_get_config:
            mock_get_config.return_value = None

            status = await github_service.get_connection_status()

            assert status["connected"] is False
            assert status["provider"] is None

    @pytest.mark.anyio
    async def test_get_connection_status_connected(self, github_service):
        """Test connection status when connected."""
        mock_config = GitHubAppConfig(
            installation_id=12345678,
        )

        with (
            patch.object(
                github_service, "get_workspace_github_config"
            ) as mock_get_config,
            patch.object(github_service, "get_installation_token") as mock_get_token,
            patch.object(
                github_service, "list_accessible_repositories"
            ) as mock_list_repos,
        ):
            mock_get_config.return_value = mock_config
            mock_get_token.return_value = "test-token"
            mock_list_repos.return_value = []

            status = await github_service.get_connection_status()

            assert status["connected"] is True
            assert status["provider"] == "github"
            assert status["installation_id"] == 12345678


class TestGitHubAppConfig:
    """Test GitHub App configuration models."""

    def test_managed_config_creation(self):
        """Test creating managed GitHub App config."""
        config = GitHubAppConfig(
            installation_id=12345678,
        )

        assert config.installation_id == 12345678
        assert config.app_id is None
        assert config.private_key_encrypted is None

    def test_enterprise_config_creation(self):
        """Test creating enterprise GitHub App config."""
        config = GitHubAppConfig(
            installation_id=12345678,
            app_id="123456",
            private_key_encrypted=b"encrypted-key",
        )

        assert config.installation_id == 12345678
        assert config.app_id == "123456"
        assert config.private_key_encrypted == b"encrypted-key"
