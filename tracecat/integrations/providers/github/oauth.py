"""GitHub OAuth providers."""

import time
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import jwt
from pydantic import SecretStr

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes
from tracecat.integrations.types import TokenResponse

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_APP_AUTH_URL = "https://github.com/apps"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_VERSION = "2026-03-10"


class GitHubOAuthProvider(AuthorizationCodeOAuthProvider):
    """GitHub OAuth provider using authorization code flow for user access."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=["repo"])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github",
        name="GitHub (Delegated)",
        description="GitHub OAuth provider using authorization code flow for user access",
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
        setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
        troubleshooting_url="https://docs.github.com/en/apps/oauth-apps/maintaining-oauth-apps/troubleshooting-authorization-request-errors",
    )
    # Endpoints stay optional to respect BaseOAuthProvider's nullable defaults
    default_authorization_endpoint: ClassVar[str | None] = GITHUB_OAUTH_AUTHORIZE_URL
    default_token_endpoint: ClassVar[str | None] = GITHUB_OAUTH_TOKEN_URL


class GitHubAppOAuthProvider(ServiceAccountOAuthProvider):
    """GitHub App provider using installation access tokens."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github",
        name="GitHub App (Service account)",
        description=(
            "Authenticate to GitHub REST APIs using a GitHub App installation token."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app",
        setup_guide_url="https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app",
        troubleshooting_url="https://docs.github.com/en/apps/maintaining-github-apps/troubleshooting-github-app-installation-token-generation",
    )
    default_authorization_endpoint: ClassVar[str | None] = GITHUB_APP_AUTH_URL
    default_token_endpoint: ClassVar[str | None] = GITHUB_API_BASE_URL

    @property
    def installation_id(self) -> str:
        value = self.service_account_info["installation_id"]
        return str(value).strip()

    @property
    def private_key(self) -> str:
        value = self.service_account_info["private_key"]
        return str(value)

    def _load_service_account_info(self, client_secret: str | None) -> dict:
        info = super()._load_service_account_info(client_secret)

        private_key = info.get("private_key")
        if not isinstance(private_key, str) or not private_key.strip():
            raise ValueError("GitHub App credentials must include 'private_key'.")

        installation_id = str(info.get("installation_id", "")).strip()
        if not installation_id:
            raise ValueError("GitHub App credentials must include 'installation_id'.")

        return info

    def _derive_client_id(self, info: dict, configured_client_id: str | None) -> str:
        app_id = str(info.get("app_id") or configured_client_id or "").strip()
        if not app_id:
            raise ValueError(
                "GitHub App ID is required as the Client ID or as 'app_id' in the JSON credentials."
            )
        return app_id

    def _create_app_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.client_id,
        }
        token = jwt.encode(payload, self.private_key, algorithm="RS256")
        if isinstance(token, bytes):
            return token.decode()
        return token

    async def get_client_credentials_token(self) -> TokenResponse:
        app_jwt = self._create_app_jwt()
        url = (
            f"{self.token_endpoint.rstrip('/')}/app/installations/"
            f"{self.installation_id}/access_tokens"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
            )
            response.raise_for_status()
            data = response.json()

        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("GitHub did not return an installation access token.")

        return TokenResponse(
            access_token=SecretStr(token),
            refresh_token=None,
            expires_in=self._compute_expires_in(data.get("expires_at")),
            scope="",
            token_type="Bearer",
        )

    @staticmethod
    def _compute_expires_in(expires_at: object) -> int:
        if not isinstance(expires_at, str) or not expires_at.strip():
            return 3600

        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return 3600

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        delta = int((expiry.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        return max(delta, 0)
