"""GitHub App service for workflow store integration."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from pydantic import SecretStr

from tracecat.authz.controls import require_access_level
from tracecat.git.models import GitUrl
from tracecat.identifiers import WorkspaceID
from tracecat.secrets.enums import SecretType
from tracecat.secrets.models import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseService
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import TracecatValidationError
from tracecat.vcs.github.client import GitHubClient, PullRequestCreate
from tracecat.vcs.github.models import (
    GitHubAppConfig,
    GitHubAppCredentials,
    GitHubInstallation,
    GitHubPullRequest,
    GitHubRepository,
)
from tracecat.workspaces.models import WorkspaceVCSConfig
from tracecat.workspaces.service import WorkspaceService


class GitHubAppError(Exception):
    """GitHub App operation error."""

    pass


class GitHubAppService(BaseService):
    """GitHub App service for workflow store integration (organization-level)."""

    service_name = "github_app"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # ============================================================================
    # Organization-level methods
    # ============================================================================

    @require_access_level(AccessLevel.ADMIN)
    async def register_app(
        self,
        app_id: str,
        private_key_pem: SecretStr,
        webhook_secret: SecretStr | None = None,
        client_id: str | None = None,
    ) -> GitHubAppConfig:
        """Register GitHub App credentials for the organization.

        Args:
            app_id: GitHub App ID
            private_key_pem: GitHub App private key in PEM format
            webhook_secret: Optional webhook secret
            client_id: Optional client ID

        Returns:
            GitHub App configuration
        """
        # Validate credentials using the Pydantic model
        credentials = GitHubAppCredentials(
            app_id=app_id,
            private_key=private_key_pem,
            webhook_secret=webhook_secret,
            client_id=client_id,
        )

        # Prepare secret keys for storage
        secret_keys = [
            SecretKeyValue(key="app_id", value=SecretStr(credentials.app_id)),
            SecretKeyValue(key="private_key", value=credentials.private_key),
        ]

        if credentials.webhook_secret:
            secret_keys.append(
                SecretKeyValue(key="webhook_secret", value=credentials.webhook_secret)
            )

        if credentials.client_id:
            secret_keys.append(
                SecretKeyValue(key="client_id", value=SecretStr(credentials.client_id))
            )

        # Store credentials as organization secret
        secrets_service = SecretsService(session=self.session, role=self.role)
        secret_create = SecretCreate(
            name="github-app-credentials",
            type=SecretType.GITHUB_APP,
            description="GitHub App credentials for workflow synchronization",
            keys=secret_keys,
            tags={"purpose": "github-app", "provider": "github"},
        )

        await secrets_service.create_org_secret(secret_create)

        # Create config with basic info (no installation_id yet)
        config = GitHubAppConfig(
            installation_id=0,  # Will be set later during installation callback
            app_id=app_id,
            client_id=client_id,
        )

        self.logger.info(
            "Registered GitHub App credentials as organization secret",
            app_id=app_id,
            has_webhook_secret=webhook_secret is not None,
            has_client_id=client_id is not None,
        )

        return config

    @require_access_level(AccessLevel.ADMIN)
    async def register_existing_app(
        self,
        app_id: str,
        private_key_pem: SecretStr,
        webhook_secret: SecretStr | None = None,
        client_id: str | None = None,
    ) -> GitHubAppConfig:
        """Register existing GitHub App credentials that were created outside of Tracecat.

        Use this method when you have an existing GitHub App that was created manually
        in GitHub's settings, and you want to store its credentials in Tracecat.

        Args:
            app_id: GitHub App ID
            private_key_pem: GitHub App private key in PEM format
            webhook_secret: Optional webhook secret
            client_id: Optional client ID

        Returns:
            GitHub App configuration

        Raises:
            GitHubAppError: If credentials already exist or are invalid
        """
        # Check if credentials already exist
        try:
            await self.get_github_app_credentials()
            raise GitHubAppError(
                "GitHub App credentials already exist. Use update_github_app_credentials() to modify them."
            )
        except GitHubAppError as e:
            if "Failed to retrieve GitHub App credentials" not in str(e):
                # Re-raise if it's not a "not found" error
                raise

        # Delegate to the main register_app method
        return await self.register_app(
            app_id, private_key_pem, webhook_secret, client_id
        )

    @require_access_level(AccessLevel.ADMIN)
    async def update_github_app_credentials(
        self,
        app_id: str | None = None,
        private_key_pem: SecretStr | None = None,
        webhook_secret: SecretStr | None = None,
        client_id: str | None = None,
    ) -> GitHubAppConfig:
        """Update existing GitHub App credentials.

        Args:
            app_id: New GitHub App ID (optional)
            private_key_pem: New GitHub App private key in PEM format (optional)
            webhook_secret: New webhook secret (optional)
            client_id: New client ID (optional)

        Returns:
            Updated GitHub App configuration

        Raises:
            GitHubAppError: If no credentials exist or update fails
        """
        # Get existing credentials
        try:
            existing_credentials = await self.get_github_app_credentials()
        except GitHubAppError as e:
            raise GitHubAppError(
                "No existing GitHub App credentials found. Use register_existing_app() first."
            ) from e

        # Use existing values if new ones aren't provided
        updated_credentials = GitHubAppCredentials(
            app_id=app_id if app_id is not None else existing_credentials.app_id,
            private_key=private_key_pem
            if private_key_pem is not None
            else existing_credentials.private_key,
            webhook_secret=webhook_secret
            if webhook_secret is not None
            else existing_credentials.webhook_secret,
            client_id=client_id
            if client_id is not None
            else existing_credentials.client_id,
        )

        # Prepare secret keys for storage
        secret_keys = [
            SecretKeyValue(key="app_id", value=SecretStr(updated_credentials.app_id)),
            SecretKeyValue(key="private_key", value=updated_credentials.private_key),
        ]

        if updated_credentials.webhook_secret:
            secret_keys.append(
                SecretKeyValue(
                    key="webhook_secret", value=updated_credentials.webhook_secret
                )
            )

        if updated_credentials.client_id:
            secret_keys.append(
                SecretKeyValue(
                    key="client_id", value=SecretStr(updated_credentials.client_id)
                )
            )

        # Update the organization secret
        secrets_service = SecretsService(session=self.session, role=self.role)
        secret = await secrets_service.get_org_secret_by_name("github-app-credentials")

        secret_update = SecretUpdate(keys=secret_keys)
        await secrets_service.update_org_secret(secret, secret_update)

        # Create config with basic info
        config = GitHubAppConfig(
            installation_id=0,  # Will be set later during installation callback
            app_id=updated_credentials.app_id,
            client_id=updated_credentials.client_id,
        )

        self.logger.info(
            "Updated GitHub App credentials",
            app_id=updated_credentials.app_id,
            has_webhook_secret=updated_credentials.webhook_secret is not None,
            has_client_id=updated_credentials.client_id is not None,
        )

        return config

    @require_access_level(AccessLevel.ADMIN)
    async def save_github_app_credentials(
        self,
        app_id: str,
        private_key_pem: SecretStr,
        webhook_secret: SecretStr | None = None,
        client_id: str | None = None,
    ) -> tuple[GitHubAppConfig, bool]:
        """Save GitHub App credentials (create if new, update if exists).

        Args:
            app_id: GitHub App ID
            private_key_pem: GitHub App private key in PEM format
            webhook_secret: Optional webhook secret
            client_id: Optional client ID

        Returns:
            Tuple of (GitHubAppConfig, was_created: bool)
            was_created is True if credentials were newly created, False if updated

        Raises:
            GitHubAppError: If operation fails
        """
        try:
            # Try to get existing credentials to determine if this is an update
            existing_credentials = await self.get_github_app_credentials()

            # Update existing credentials
            config = await self.update_github_app_credentials(
                app_id=app_id,
                private_key_pem=private_key_pem,
                webhook_secret=webhook_secret,
                client_id=client_id,
            )

            self.logger.info(
                "Updated existing GitHub App credentials",
                app_id=app_id,
                previous_app_id=existing_credentials.app_id,
            )

            return config, False  # was_created = False (updated)

        except GitHubAppError as e:
            if "Failed to retrieve GitHub App credentials" in str(e):
                # No existing credentials, create new ones
                config = await self.register_existing_app(
                    app_id=app_id,
                    private_key_pem=private_key_pem,
                    webhook_secret=webhook_secret,
                    client_id=client_id,
                )

                self.logger.info(
                    "Created new GitHub App credentials",
                    app_id=app_id,
                )

                return config, True  # was_created = True (new)
            else:
                # Re-raise other GitHubAppErrors
                raise

    async def get_github_app_credentials_status(self) -> dict[str, Any]:
        """Get the status of GitHub App credentials.

        Returns:
            Dictionary with credentials status information
        """
        try:
            credentials = await self.get_github_app_credentials()

            # Get the secret to find when it was created
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service.get_org_secret_by_name(
                "github-app-credentials"
            )

            return {
                "exists": True,
                "app_id": credentials.app_id,
                "has_webhook_secret": credentials.webhook_secret is not None,
                "has_client_id": credentials.client_id is not None,
                "created_at": secret.created_at.isoformat()
                if secret.created_at
                else None,
            }
        except GitHubAppError:
            return {
                "exists": False,
                "app_id": None,
                "has_webhook_secret": False,
                "has_client_id": False,
                "created_at": None,
            }

    async def get_github_app_credentials(self) -> GitHubAppCredentials:
        """Retrieve GitHub App credentials from organization secret.

        Returns:
            GitHub App credentials

        Raises:
            GitHubAppError: If credentials are not found or invalid
        """
        try:
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service.get_org_secret_by_name(
                "github-app-credentials"
            )

            # Decrypt the secret keys
            decrypted_keys = secrets_service.decrypt_keys(secret.encrypted_keys)

            # Convert to dictionary for easier access
            key_dict = {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}

            # Validate and construct the credentials model
            credentials = GitHubAppCredentials.model_validate(key_dict)

            self.logger.debug(
                "Retrieved GitHub App credentials from organization secret"
            )
            return credentials

        except Exception as e:
            self.logger.error("Failed to retrieve GitHub App credentials", error=str(e))
            raise GitHubAppError(
                f"Failed to retrieve GitHub App credentials: {e}"
            ) from e

    @require_access_level(AccessLevel.ADMIN)
    async def set_workspace_installation(
        self,
        workspace_id: WorkspaceID,
        installation_id: int,
    ) -> GitHubAppConfig:
        """Set GitHub App installation ID for a workspace.

        Args:
            workspace_id: Workspace ID
            installation_id: GitHub App installation ID

        Returns:
            GitHub App configuration
        """
        # Get app credentials from organization secret to validate they exist
        credentials = await self.get_github_app_credentials()

        # Verify the installation exists
        installation = await self._get_installation_details(installation_id)

        config = GitHubAppConfig(
            installation_id=installation_id,
            app_id=credentials.app_id,
            client_id=credentials.client_id,
            installation=installation,
        )

        # Save the configuration to the workspace
        await self.save_workspace_github_config(workspace_id, config)

        self.logger.info(
            "Set GitHub App installation for workspace",
            installation_id=installation_id,
            workspace_id=workspace_id,
            app_id=credentials.app_id,
            account=installation.account_login,
        )

        return config

    @require_access_level(AccessLevel.ADMIN)
    async def uninstall_app(self, workspace_id: WorkspaceID) -> None:
        """Remove GitHub App configuration from workspace."""
        if not workspace_id:
            raise TracecatValidationError("Workspace ID is required")

        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(workspace_id)

        if not workspace:
            raise TracecatValidationError("Workspace not found")

        # Remove GitHub App configuration
        vcs_config = workspace.settings.vcs or WorkspaceVCSConfig(provider="github")
        if vcs_config.github_app:
            vcs_config.github_app = None

        if not vcs_config or vcs_config == {}:
            # Remove entire VCS config if empty
            workspace.settings.vcs = None
        else:
            workspace.settings.vcs = vcs_config

        self.session.add(workspace)
        await self.session.commit()

        self.logger.info("Uninstalled GitHub App from workspace")

    # ============================================================================
    # Workspace-level methods
    # ============================================================================

    async def get_workspace_github_config(
        self, workspace_id: WorkspaceID
    ) -> GitHubAppConfig:
        """Get GitHub App configuration for workspace."""
        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(workspace_id)

        if not workspace:
            raise GitHubAppError("Workspace not found")

        vcs_config = workspace.settings.vcs
        if not vcs_config:
            raise GitHubAppError("Workspace VCS configuration not found")

        github_config = vcs_config.github_app

        if not github_config:
            raise GitHubAppError("GitHub App configuration not found")

        return github_config

    async def save_workspace_github_config(
        self, workspace_id: WorkspaceID, config: GitHubAppConfig
    ) -> None:
        """Save GitHub App configuration to workspace."""
        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(workspace_id)

        if not workspace:
            raise TracecatValidationError("Workspace not found")

        # Update VCS configuration
        vcs_config = workspace.settings.vcs or WorkspaceVCSConfig(provider="github")

        vcs_config.github_app = config

        workspace.settings.vcs = vcs_config

        self.session.add(workspace)
        await self.session.commit()

        self.logger.info(
            "Saved GitHub App configuration",
            installation_id=config.installation_id,
        )

    async def get_installation_token(self, workspace_id: WorkspaceID) -> str:
        """Get GitHub installation access token for workspace."""
        # Get installation ID from workspace config
        config = await self.get_workspace_github_config(workspace_id)
        if not config.installation_id:
            raise GitHubAppError(
                "GitHub App installation ID not configured for workspace"
            )

        # Get app credentials from organization secret
        credentials = await self.get_github_app_credentials()

        # Generate JWT and get installation token
        app_jwt = self._generate_jwt(
            credentials.app_id, credentials.private_key.get_secret_value()
        )
        return await self._get_installation_token(config.installation_id, app_jwt)

    async def list_accessible_repositories(
        self, workspace_id: WorkspaceID
    ) -> list[GitHubRepository]:
        """List repositories accessible to the GitHub App installation."""
        config = await self.get_workspace_github_config(workspace_id)
        if not config:
            raise GitHubAppError("No GitHub App configuration found")

        token = await self.get_installation_token(workspace_id)

        # Use a temporary client to list repositories
        async with GitHubClient(token, "temp", "temp") as client:
            return await client.list_repositories(config.installation_id)

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        repo_url: GitUrl,
        workspace_id: WorkspaceID,
    ) -> GitHubPullRequest:
        """Create a pull request using GitHub App authentication.

        Args:
            title: Pull request title
            body: Pull request body
            head_branch: Source branch
            base_branch: Target branch
            repo_url: Repository URL
            workspace_id: Workspace ID

        Returns:
            Created pull request data
        """
        # Get installation token
        token = await self.get_installation_token(workspace_id)

        # Create GitHub client
        async with GitHubClient(token, repo_url.org, repo_url.repo) as client:
            pr_data = PullRequestCreate(
                title=title,
                body=body,
                head=head_branch,
                base=base_branch,
            )

            return await client.create_pull_request(pr_data)

    async def get_connection_status(self, workspace_id: WorkspaceID) -> dict[str, Any]:
        """Get GitHub App connection status for workspace."""
        config = await self.get_workspace_github_config(workspace_id)

        if not config:
            return {
                "connected": False,
                "provider": None,
            }

        try:
            # Test connection by getting installation token
            await self.get_installation_token(workspace_id)

            # Get accessible repositories
            repositories = await self.list_accessible_repositories(workspace_id)

            return {
                "connected": True,
                "provider": "github",
                "installation_id": config.installation_id,
                "installation": config.installation.model_dump()
                if config.installation
                else None,
                "accessible_repositories": [repo.full_name for repo in repositories],
                "repository_count": len(repositories),
            }

        except Exception as e:
            self.logger.error("GitHub App connection test failed", error=str(e))
            return {
                "connected": False,
                "provider": "github",
                "error": str(e),
            }

    async def set_installation_id(
        self, installation_id: int, workspace_id: WorkspaceID
    ) -> GitHubAppConfig:
        """Set installation ID for existing GitHub App configuration.

        Args:
            installation_id: GitHub App installation ID
            workspace_id: Workspace ID

        Returns:
            Updated GitHub App configuration
        """
        # Get existing config
        config = await self.get_workspace_github_config(workspace_id)

        # Update installation_id
        config.installation_id = installation_id

        # Optionally fetch installation details
        try:
            installation = await self._get_installation_details(installation_id)
            config.installation = installation
        except Exception as e:
            self.logger.warning(
                "Could not fetch installation details",
                installation_id=installation_id,
                error=str(e),
            )

        await self.save_workspace_github_config(workspace_id, config)

        self.logger.info(
            "Set installation ID for GitHub App",
            installation_id=installation_id,
        )

        return config

    # ============================================================================
    # Private helper methods
    # ============================================================================

    def _generate_jwt(self, app_id: str, private_key: str) -> str:
        """Generate JWT for GitHub App authentication."""
        now = int(time.time())

        payload = {
            "iss": app_id,
            "iat": now - 60,  # Issued 60 seconds ago
            "exp": now + (10 * 60),  # Expires in 10 minutes
        }

        try:
            token = jwt.encode(payload, private_key, algorithm="RS256")
            return token
        except Exception as e:
            raise GitHubAppError(f"Failed to generate JWT: {e}") from e

    async def _get_installation_token(self, installation_id: int, app_jwt: str) -> str:
        """Get installation access token using app JWT."""
        url = (
            f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        )

        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, timeout=30.0)
                response.raise_for_status()

                data = response.json()
                token = data["token"]

                self.logger.debug(
                    "Generated installation token",
                    installation_id=installation_id,
                    expires_at=data.get("expires_at"),
                )

                return token

            except httpx.HTTPStatusError as e:
                self.logger.error(
                    "Failed to get installation token",
                    installation_id=installation_id,
                    status_code=e.response.status_code,
                    response=e.response.text,
                )
                raise GitHubAppError(
                    f"Failed to get installation token: {e.response.status_code}"
                ) from e
            except Exception as e:
                self.logger.error(
                    "Error getting installation token",
                    installation_id=installation_id,
                    error=str(e),
                )
                raise GitHubAppError(f"Error getting installation token: {e}") from e

    async def _get_installation_details(
        self, installation_id: int
    ) -> GitHubInstallation:
        """Get installation details from GitHub API."""
        # This would require an app JWT, so for now we'll create a minimal installation
        # In a real implementation, you'd fetch this from GitHub
        return GitHubInstallation(
            id=installation_id,
            account_login="unknown",
            account_type="Organization",
            target_type="Organization",
        )
