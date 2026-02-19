"""Tests for GitHub App integration."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github import Github
from github.GithubException import GithubException, UnknownObjectException
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Secret
from tracecat.exceptions import TracecatNotFoundError
from tracecat.git.types import GitUrl
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.vcs.github.schemas import (
    GitHubAppConfig,
    GitHubAppCredentials,
    GitHubInstallation,
    GitHubRepository,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def github_service(session: AsyncSession, svc_role: Role):
    """Create GitHub App service instance."""
    return GitHubAppService(session=session, role=svc_role)


@pytest.fixture
def github_admin_service(session: AsyncSession, svc_admin_role: Role):
    """Create GitHub App service instance with admin privileges."""
    return GitHubAppService(session=session, role=svc_admin_role)


@pytest.fixture
def mock_credentials():
    """Mock GitHub App credentials."""
    return GitHubAppCredentials(
        app_id="123456",
        private_key=SecretStr(
            "-----BEGIN RSA PRIVATE KEY-----\ntest-key\n-----END RSA PRIVATE KEY-----"
        ),
        webhook_secret=SecretStr("webhook-secret"),
        client_id="client-123",
    )


@pytest.fixture
def mock_installation():
    """Mock GitHub App installation."""
    return GitHubInstallation(
        id=12345678,
        account_login="test-org",
        account_type="Organization",
        target_type="Organization",
        created_at=datetime.now(),
        repositories=[
            GitHubRepository(
                id=1,
                name="test-repo",
                full_name="test-org/test-repo",
                private=False,
                default_branch="main",
            )
        ],
    )


@pytest.fixture
def mock_repo_url():
    """Mock repository URL."""
    return GitUrl(host="github.com", org="test-org", repo="test-repo")


@pytest.fixture
def mock_secret():
    """Mock organization secret."""
    return Secret(
        id="secret-123",
        name="github-app-credentials",
        type=SecretType.GITHUB_APP,
        description="GitHub App credentials",
        encrypted_keys=b"encrypted-data",
        created_at=datetime.now(),
        tags={},
    )  # type: ignore


class TestGitHubAppService:
    """Test GitHub App service functionality."""

    # ============================================================================
    # Credential Management Tests
    # ============================================================================

    @pytest.mark.anyio
    async def test_register_app_success(self, github_admin_service, mock_credentials):
        """Test registering GitHub App credentials."""
        with patch("tracecat.vcs.github.app.SecretsService") as mock_secrets_service:
            mock_service = AsyncMock()
            mock_secrets_service.return_value = mock_service

            config = await github_admin_service.register_app(
                app_id=mock_credentials.app_id,
                private_key_pem=mock_credentials.private_key,
                webhook_secret=mock_credentials.webhook_secret,
                client_id=mock_credentials.client_id,
            )

            assert config.app_id == mock_credentials.app_id
            assert config.client_id == mock_credentials.client_id
            assert config.installation_id == 0  # Not set during registration
            mock_service.create_org_secret.assert_called_once()

    @pytest.mark.anyio
    async def test_get_github_app_credentials_success(
        self, github_service, mock_credentials, mock_secret
    ):
        """Test retrieving GitHub App credentials."""
        with patch("tracecat.vcs.github.app.SecretsService") as mock_secrets_service:
            mock_service = Mock()
            mock_service.get_org_secret_by_name = AsyncMock(return_value=mock_secret)
            mock_service.decrypt_keys = Mock(
                return_value=[
                    SecretKeyValue(
                        key="app_id", value=SecretStr(mock_credentials.app_id)
                    ),
                    SecretKeyValue(
                        key="private_key", value=mock_credentials.private_key
                    ),
                    SecretKeyValue(
                        key="webhook_secret", value=mock_credentials.webhook_secret
                    ),
                    SecretKeyValue(
                        key="client_id", value=SecretStr(mock_credentials.client_id)
                    ),
                ]
            )
            mock_secrets_service.return_value = mock_service

            credentials = await github_service.get_github_app_credentials()

            assert credentials.app_id == mock_credentials.app_id
            assert (
                credentials.private_key.get_secret_value()
                == mock_credentials.private_key.get_secret_value()
            )
            assert (
                credentials.webhook_secret.get_secret_value()
                == mock_credentials.webhook_secret.get_secret_value()
            )
            assert credentials.client_id == mock_credentials.client_id

    @pytest.mark.anyio
    async def test_get_github_app_credentials_not_found(self, github_service):
        """Test retrieving GitHub App credentials when not found."""
        with patch("tracecat.vcs.github.app.SecretsService") as mock_secrets_service:
            mock_service = AsyncMock()
            mock_service.get_org_secret_by_name.side_effect = TracecatNotFoundError(
                "Secret not found"
            )
            mock_secrets_service.return_value = mock_service

            with pytest.raises(
                GitHubAppError, match="Failed to retrieve GitHub App credentials"
            ):
                await github_service.get_github_app_credentials()

    @pytest.mark.anyio
    async def test_update_github_app_credentials_success(
        self, github_admin_service, mock_credentials, mock_secret
    ):
        """Test updating existing GitHub App credentials."""
        new_app_id = "654321"

        with patch("tracecat.vcs.github.app.SecretsService") as mock_secrets_service:
            mock_service = Mock()
            mock_service.get_org_secret_by_name = AsyncMock(return_value=mock_secret)
            mock_service.decrypt_keys = Mock(
                return_value=[
                    SecretKeyValue(
                        key="app_id", value=SecretStr(mock_credentials.app_id)
                    ),
                    SecretKeyValue(
                        key="private_key", value=mock_credentials.private_key
                    ),
                    SecretKeyValue(
                        key="webhook_secret", value=mock_credentials.webhook_secret
                    ),
                    SecretKeyValue(
                        key="client_id", value=SecretStr(mock_credentials.client_id)
                    ),
                ]
            )
            mock_service.update_org_secret = AsyncMock()
            mock_secrets_service.return_value = mock_service

            # Mock get_github_app_credentials to return existing credentials
            with patch.object(
                github_admin_service,
                "get_github_app_credentials",
                return_value=mock_credentials,
            ):
                config = await github_admin_service.update_github_app_credentials(
                    app_id=new_app_id
                )

                assert config.app_id == new_app_id
                mock_service.update_org_secret.assert_called_once()

    @pytest.mark.anyio
    async def test_get_github_app_credentials_status_exists(
        self, github_service, mock_credentials, mock_secret
    ):
        """Test getting credentials status when they exist."""
        with patch.object(
            github_service, "get_github_app_credentials", return_value=mock_credentials
        ):
            with patch(
                "tracecat.vcs.github.app.SecretsService"
            ) as mock_secrets_service:
                mock_service = AsyncMock()
                mock_service.get_org_secret_by_name.return_value = mock_secret
                mock_secrets_service.return_value = mock_service

                status = await github_service.get_github_app_credentials_status()

                assert status["exists"] is True
                assert status["app_id"] == mock_credentials.app_id
                assert status["has_webhook_secret"] is True
                assert status["webhook_secret_preview"] == "webh****"
                assert status["client_id"] == mock_credentials.client_id

    @pytest.mark.anyio
    async def test_get_github_app_credentials_status_not_exists(self, github_service):
        """Test getting credentials status when they don't exist."""
        with patch.object(
            github_service,
            "get_github_app_credentials",
            side_effect=GitHubAppError("Not found"),
        ):
            status = await github_service.get_github_app_credentials_status()

            assert status["exists"] is False
            assert status["app_id"] is None
            assert status["has_webhook_secret"] is False
            assert status["webhook_secret_preview"] is None
            assert status["client_id"] is None

    # ============================================================================
    # Installation Management Tests
    # ============================================================================

    # ============================================================================
    # Repository Access Tests
    # ============================================================================

    @pytest.mark.anyio
    async def test_get_github_client_for_repo_success(
        self, github_service, mock_credentials, mock_repo_url
    ):
        """Test getting GitHub client for repository."""
        mock_github_client = Mock(spec=Github)
        mock_installation = Mock()
        mock_installation.get_github_for_installation.return_value = mock_github_client

        with (
            patch.object(
                github_service,
                "get_github_app_credentials",
                return_value=mock_credentials,
            ),
            patch("tracecat.vcs.github.app.GithubIntegration") as mock_gh_integration,
            patch("tracecat.vcs.github.app.asyncio.to_thread") as mock_to_thread,
        ):
            mock_integration = Mock()
            mock_integration.get_repo_installation.return_value = mock_installation
            mock_gh_integration.return_value = mock_integration
            mock_to_thread.return_value = mock_installation

            client = await github_service.get_github_client_for_repo(mock_repo_url)

            assert client == mock_github_client
            mock_to_thread.assert_called_once()
            mock_installation.get_github_for_installation.assert_called_once()

    @pytest.mark.anyio
    async def test_get_github_client_for_repo_not_installed(
        self, github_service, mock_credentials, mock_repo_url
    ):
        """Test getting GitHub client when app not installed on repo."""
        with (
            patch.object(
                github_service,
                "get_github_app_credentials",
                return_value=mock_credentials,
            ),
            patch("tracecat.vcs.github.app.GithubIntegration") as mock_gh_integration,
            patch("tracecat.vcs.github.app.asyncio.to_thread") as mock_to_thread,
        ):
            mock_integration = Mock()
            mock_gh_integration.return_value = mock_integration
            mock_to_thread.side_effect = UnknownObjectException(
                404, {"message": "Not Found"}, {}
            )

            with pytest.raises(GitHubAppError, match="App is not installed"):
                await github_service.get_github_client_for_repo(mock_repo_url)

    @pytest.mark.anyio
    async def test_get_github_client_for_repo_github_error(
        self, github_service, mock_credentials, mock_repo_url
    ):
        """Test getting GitHub client with GitHub API error."""
        with (
            patch.object(
                github_service,
                "get_github_app_credentials",
                return_value=mock_credentials,
            ),
            patch("tracecat.vcs.github.app.GithubIntegration") as mock_gh_integration,
            patch("tracecat.vcs.github.app.asyncio.to_thread") as mock_to_thread,
        ):
            mock_integration = Mock()
            mock_gh_integration.return_value = mock_integration
            mock_to_thread.side_effect = GithubException(
                500, {"message": "Server Error"}, {}
            )

            with pytest.raises(GitHubAppError, match="GitHub API error"):
                await github_service.get_github_client_for_repo(mock_repo_url)

    # ============================================================================
    # Permission Error Tests
    # ============================================================================

    @pytest.mark.anyio
    async def test_register_app_permission_denied(
        self, github_service, mock_credentials
    ):
        """Test that non-admin role cannot register app."""
        from tracecat.exceptions import TracecatAuthorizationError

        with pytest.raises(
            TracecatAuthorizationError,
            match="You don't have permission to perform this action.",
        ):
            await github_service.register_app(
                app_id=mock_credentials.app_id,
                private_key_pem=mock_credentials.private_key,
                webhook_secret=mock_credentials.webhook_secret,
                client_id=mock_credentials.client_id,
            )

    @pytest.mark.anyio
    async def test_update_github_app_credentials_permission_denied(
        self, github_service
    ):
        """Test that non-admin role cannot update credentials."""
        from tracecat.exceptions import TracecatAuthorizationError

        with pytest.raises(
            TracecatAuthorizationError,
            match="You don't have permission to perform this action.",
        ):
            await github_service.update_github_app_credentials(app_id="new-id")


class TestGitHubAppModels:
    """Test GitHub App data models."""

    def test_github_app_config_creation(self):
        """Test creating GitHub App config."""
        config = GitHubAppConfig(
            installation_id=12345678, app_id="123456", client_id="client-123"
        )

        assert config.installation_id == 12345678
        assert config.app_id == "123456"
        assert config.client_id == "client-123"
        assert config.private_key_encrypted is None

    def test_github_app_credentials_creation(self):
        """Test creating GitHub App credentials."""
        credentials = GitHubAppCredentials(
            app_id="123456",
            private_key=SecretStr("private-key"),
            webhook_secret=SecretStr("webhook-secret"),
            client_id="client-123",
        )

        assert credentials.app_id == "123456"
        assert credentials.private_key.get_secret_value() == "private-key"
        assert (
            credentials.webhook_secret
            and credentials.webhook_secret.get_secret_value() == "webhook-secret"
        )
        assert credentials.client_id == "client-123"

    def test_github_installation_creation(self):
        """Test creating GitHub installation."""
        installation = GitHubInstallation(
            id=12345678,
            account_login="test-org",
            account_type="Organization",
            target_type="Organization",
        )

        assert installation.id == 12345678
        assert installation.account_login == "test-org"
        assert installation.account_type == "Organization"
        assert installation.target_type == "Organization"
        assert installation.permissions == {}
        assert installation.repositories == []

    def test_github_repository_creation(self):
        """Test creating GitHub repository."""
        repo = GitHubRepository(
            id=123,
            name="test-repo",
            full_name="test-org/test-repo",
            private=False,
            default_branch="main",
        )

        assert repo.id == 123
        assert repo.name == "test-repo"
        assert repo.full_name == "test-org/test-repo"
        assert repo.private is False
        assert repo.default_branch == "main"
