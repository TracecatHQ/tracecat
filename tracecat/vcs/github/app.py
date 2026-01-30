"""GitHub App service for workflow store integration."""

from __future__ import annotations

import asyncio
from typing import Any

from github import Auth, Github, GithubIntegration
from github.GithubException import GithubException, UnknownObjectException
from pydantic import SecretStr

from tracecat.auth.types import AccessLevel
from tracecat.authz.controls import require_access_level
from tracecat.exceptions import TracecatException
from tracecat.git.types import GitUrl
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService
from tracecat.vcs.github.schemas import (
    GitHubAppConfig,
    GitHubAppCredentials,
)


class GitHubAppError(TracecatException):
    """GitHub App operation error."""


class GitHubAppService(BaseOrgService):
    """GitHub App service for workflow store integration (organization-level)."""

    service_name = "github_app"

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

    @require_access_level(AccessLevel.ADMIN)
    async def delete_github_app_credentials(self) -> None:
        """Delete GitHub App credentials for the organization.

        Raises:
            GitHubAppError: If no credentials exist or deletion fails
        """
        try:
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service.get_org_secret_by_name(
                "github-app-credentials"
            )
            await secrets_service.delete_org_secret(secret)
            self.logger.info("Deleted GitHub App credentials")
        except Exception as e:
            self.logger.error("Failed to delete GitHub App credentials", error=str(e))
            raise GitHubAppError(f"Failed to delete GitHub App credentials: {e}") from e

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

            has_webhook_secret = credentials.webhook_secret is not None
            webhook_secret_preview = None
            if credentials.webhook_secret is not None:
                raw = credentials.webhook_secret.get_secret_value()
                webhook_secret_preview = raw[:4] + "****" if len(raw) >= 4 else "****"

            return {
                "exists": True,
                "app_id": credentials.app_id,
                "has_webhook_secret": has_webhook_secret,
                "webhook_secret_preview": webhook_secret_preview,
                "client_id": credentials.client_id,
                "created_at": secret.created_at.isoformat()
                if secret.created_at
                else None,
            }
        except GitHubAppError:
            return {
                "exists": False,
                "app_id": None,
                "has_webhook_secret": False,
                "webhook_secret_preview": None,
                "client_id": None,
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
            self.logger.debug("Failed to retrieve GitHub App credentials", error=str(e))
            raise GitHubAppError(
                f"Failed to retrieve GitHub App credentials: {e}"
            ) from e

    async def get_github_client_for_repo(self, repo_url: GitUrl) -> Github:
        """Get authenticated PyGithub client for a specific repository.

        Args:
            repo_url: Git repository URL

        Returns:
            Authenticated PyGithub client for the repository

        Raises:
            GitHubAppError: If authentication fails or app not installed
        """
        credentials = await self.get_github_app_credentials()

        # Normalize private key format to handle common formatting issues
        raw_private_key = credentials.private_key.get_secret_value()
        normalized_private_key = self._normalize_private_key(raw_private_key)

        # Create GithubIntegration
        auth = Auth.AppAuth(
            app_id=int(credentials.app_id),
            private_key=normalized_private_key,
        )
        gh_integration = GithubIntegration(auth=auth)

        try:
            # Get installation for the repository
            installation = await asyncio.to_thread(
                gh_integration.get_repo_installation,
                owner=repo_url.org,
                repo=repo_url.repo,
            )

            self.logger.debug(
                "Retrieved installation for repository",
                installation_id=installation.id,
                repo=f"{repo_url.org}/{repo_url.repo}",
            )

            # Get authenticated GitHub client for this installation
            # This handles token generation automatically
            return installation.get_github_for_installation()

        except UnknownObjectException as e:
            if e.status == 404:
                raise GitHubAppError(
                    f"App is not installed on {repo_url.org}/{repo_url.repo}"
                ) from e
            raise GitHubAppError(f"Error getting GitHub client: {e}") from e
        except GithubException as e:
            self.logger.error(
                "GitHub API error getting client for repository",
                status=e.status,
                data=e.data,
                repo=f"{repo_url.org}/{repo_url.repo}",
            )
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh_integration.close()

    def _normalize_private_key(self, private_key: str) -> str:
        """Normalize private key format to handle common formatting issues.

        Args:
            private_key: Raw private key string from secret storage

        Returns:
            Normalized private key string
        """
        # Remove any leading/trailing whitespace
        normalized = private_key.strip()

        # Convert Windows line endings to Unix
        normalized = normalized.replace("\r\n", "\n")

        # Remove any extra whitespace from individual lines while preserving structure
        lines = normalized.split("\n")
        cleaned_lines = []

        for line in lines:
            # Only strip whitespace, don't remove empty lines as they may be significant
            cleaned_lines.append(line.rstrip())

        # Rejoin with Unix line endings
        normalized = "\n".join(cleaned_lines)

        # Ensure the key ends with a newline (some parsers expect this)
        if not normalized.endswith("\n"):
            normalized += "\n"

        self.logger.debug(
            "Normalized private key format",
            original_length=len(private_key),
            normalized_length=len(normalized),
            has_crlf="\r\n" in private_key,
        )

        return normalized
