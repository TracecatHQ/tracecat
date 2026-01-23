"""HTTP client for Tracecat Admin API."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from tracecat_admin.config import Config, get_config, load_cookies
from tracecat_admin.schemas import (
    OrgInviteResponse,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    RegistrySettingsRead,
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
    UserRead,
)


class AdminClientError(Exception):
    """Base exception for admin client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminClient:
    """Async HTTP client for Tracecat Admin API.

    Uses service key authentication via x-tracecat-service-key header.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or get_config()
        self._client: httpx.AsyncClient | None = None

    @property
    def config(self) -> Config:
        return self._config

    async def __aenter__(self) -> AdminClient:
        # Prefer cookie auth over service key
        cookies = load_cookies()
        if cookies:
            self._client = httpx.AsyncClient(
                base_url=self._config.api_url,
                cookies=cookies,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        elif self._config.service_key:
            # Fall back to service key auth
            self._client = httpx.AsyncClient(
                base_url=self._config.api_url,
                headers={
                    "x-tracecat-service-key": self._config.service_key,
                    "x-tracecat-role-service-id": "tracecat-admin-cli",
                    "x-tracecat-role-access-level": "ADMIN",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        else:
            raise AdminClientError(
                "Not authenticated. Run 'tracecat auth login' or set TRACECAT__SERVICE_KEY"
            )
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise AdminClientError("Client not initialized. Use async context manager.")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request and handle errors."""
        client = self._ensure_client()
        response = await client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise AdminClientError(
                f"API error: {detail}",
                status_code=response.status_code,
            )
        return response

    # User endpoints
    async def list_users(self) -> list[UserRead]:
        """List all users."""
        response = await self._request("GET", "/admin/users")
        return [UserRead.model_validate(u) for u in response.json()]

    async def get_user(self, user_id: str) -> UserRead:
        """Get a user by ID."""
        response = await self._request("GET", f"/admin/users/{user_id}")
        return UserRead.model_validate(response.json())

    async def promote_user(self, user_id: str) -> UserRead:
        """Promote a user to superuser."""
        response = await self._request("POST", f"/admin/users/{user_id}/promote")
        return UserRead.model_validate(response.json())

    async def demote_user(self, user_id: str) -> UserRead:
        """Demote a user from superuser."""
        response = await self._request("POST", f"/admin/users/{user_id}/demote")
        return UserRead.model_validate(response.json())

    # Organization endpoints
    async def list_organizations(self) -> list[OrgRead]:
        """List all organizations."""
        response = await self._request("GET", "/admin/organizations")
        return [OrgRead.model_validate(o) for o in response.json()]

    async def create_organization(self, name: str, slug: str) -> OrgRead:
        """Create a new organization."""
        response = await self._request(
            "POST",
            "/admin/organizations",
            json={"name": name, "slug": slug},
        )
        return OrgRead.model_validate(response.json())

    async def get_organization(self, org_id: str) -> OrgRead:
        """Get an organization by ID."""
        response = await self._request("GET", f"/admin/organizations/{org_id}")
        return OrgRead.model_validate(response.json())

    async def update_organization(
        self,
        org_id: str,
        name: str | None = None,
        slug: str | None = None,
        is_active: bool | None = None,
    ) -> OrgRead:
        """Update an organization."""
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if slug is not None:
            data["slug"] = slug
        if is_active is not None:
            data["is_active"] = is_active
        response = await self._request(
            "PATCH", f"/admin/organizations/{org_id}", json=data
        )
        return OrgRead.model_validate(response.json())

    async def delete_organization(self, org_id: str) -> None:
        """Delete an organization."""
        await self._request("DELETE", f"/admin/organizations/{org_id}")

    # Registry endpoints
    async def sync_registry(
        self, repository_id: str | None = None, force: bool = False
    ) -> RegistrySyncResponse:
        """Sync registry repositories."""
        if repository_id:
            path = f"/admin/registry/sync/{repository_id}"
        else:
            path = "/admin/registry/sync"
        params: dict[str, Any] = {}
        if force:
            params["force"] = "true"
        response = await self._request("POST", path, params=params if params else None)
        return RegistrySyncResponse.model_validate(response.json())

    async def get_registry_status(self) -> RegistryStatusResponse:
        """Get registry status."""
        response = await self._request("GET", "/admin/registry/status")
        return RegistryStatusResponse.model_validate(response.json())

    async def list_registry_versions(
        self, repository_id: str | None = None, limit: int = 50
    ) -> list[RegistryVersionRead]:
        """List registry versions."""
        params: dict[str, Any] = {"limit": limit}
        if repository_id:
            params["repository_id"] = repository_id
        response = await self._request("GET", "/admin/registry/versions", params=params)
        return [RegistryVersionRead.model_validate(v) for v in response.json()]

    async def promote_registry_version(
        self, repository_id: str, version_id: str
    ) -> RegistryVersionPromoteResponse:
        """Promote a registry version to be the current version for a repository."""
        response = await self._request(
            "POST", f"/admin/registry/{repository_id}/versions/{version_id}/promote"
        )
        return RegistryVersionPromoteResponse.model_validate(response.json())

    # Settings endpoints
    async def get_registry_settings(self) -> RegistrySettingsRead:
        """Get platform registry settings."""
        response = await self._request("GET", "/admin/settings/registry")
        return RegistrySettingsRead.model_validate(response.json())

    async def update_registry_settings(
        self,
        git_repo_url: str | None = None,
        git_repo_package_name: str | None = None,
        git_allowed_domains: set[str] | None = None,
    ) -> RegistrySettingsRead:
        """Update platform registry settings."""
        data: dict[str, Any] = {}
        if git_repo_url is not None:
            data["git_repo_url"] = git_repo_url
        if git_repo_package_name is not None:
            data["git_repo_package_name"] = git_repo_package_name
        if git_allowed_domains is not None:
            data["git_allowed_domains"] = list(git_allowed_domains)
        response = await self._request("PATCH", "/admin/settings/registry", json=data)
        return RegistrySettingsRead.model_validate(response.json())

    # Org Registry endpoints
    async def list_org_repositories(
        self, org_id: str
    ) -> list[OrgRegistryRepositoryRead]:
        """List registry repositories for an organization."""
        response = await self._request(
            "GET", f"/admin/organizations/{org_id}/registry/repositories"
        )
        return [OrgRegistryRepositoryRead.model_validate(r) for r in response.json()]

    async def list_org_repository_versions(
        self, org_id: str, repository_id: str
    ) -> list[RegistryVersionRead]:
        """List versions for a specific repository in an organization."""
        response = await self._request(
            "GET",
            f"/admin/organizations/{org_id}/registry/repositories/{repository_id}/versions",
        )
        return [RegistryVersionRead.model_validate(v) for v in response.json()]

    async def sync_org_repository(
        self, org_id: str, repository_id: str, force: bool = False
    ) -> OrgRegistrySyncResponse:
        """Sync a registry repository for an organization."""
        data: dict[str, Any] = {}
        if force:
            data["force"] = True
        response = await self._request(
            "POST",
            f"/admin/organizations/{org_id}/registry/repositories/{repository_id}/sync",
            json=data if data else None,
        )
        return OrgRegistrySyncResponse.model_validate(response.json())

    async def promote_org_repository_version(
        self, org_id: str, repository_id: str, version_id: str
    ) -> OrgRegistryVersionPromoteResponse:
        """Promote a registry version to be the current version for an org repository."""
        response = await self._request(
            "POST",
            f"/admin/organizations/{org_id}/registry/repositories/{repository_id}/versions/{version_id}/promote",
        )
        return OrgRegistryVersionPromoteResponse.model_validate(response.json())

    # Invitation endpoints
    async def invite_org_user(
        self,
        email: str,
        role: str,
        org_name: str | None = None,
        org_slug: str | None = None,
    ) -> OrgInviteResponse:
        """Invite a user to an organization.

        If the organization doesn't exist, creates it first.
        """
        data: dict[str, Any] = {"email": email, "role": role}
        if org_name is not None:
            data["org_name"] = org_name
        if org_slug is not None:
            data["org_slug"] = org_slug
        response = await self._request(
            "POST",
            "/admin/organizations/invitations",
            json=data,
        )
        return OrgInviteResponse.model_validate(response.json())
