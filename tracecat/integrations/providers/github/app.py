"""GitHub App provider that mints installation access tokens."""

import re
import time
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlparse, urlunparse

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import SecretStr

from tracecat.integrations.providers.base import (
    ClientCredentials,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes
from tracecat.integrations.types import TokenResponse

GITHUB_APP_INSTALLATIONS_ENDPOINT = "https://api.github.com/app/installations"
GITHUB_API_VERSION = "2022-11-28"

_JWT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540
_REQUEST_TIMEOUT_SECONDS = 30.0
_INSTALLATIONS_PATH = re.compile(r"^(?P<prefix>.*)/app/installations/?$")
_ACCESS_TOKENS_PATH = re.compile(
    r"^(?P<prefix>.*)/app/installations/(?P<installation_id>\d+)/access_tokens/?$"
)
_PERMISSION_SCOPE = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*):(?P<level>read|write|admin)$"
)


def normalize_github_app_private_key(private_key: str) -> str:
    """Normalize a pasted GitHub App PEM private key."""
    normalized = private_key.strip().replace("\r\n", "\n")
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")
    return f"{normalized}\n"


def parse_github_app_permissions(scopes: list[str]) -> dict[str, str]:
    """Parse ``<permission>:<level>`` scopes into installation token permissions."""
    permissions: dict[str, str] = {}
    for scope in scopes:
        match = _PERMISSION_SCOPE.fullmatch(scope.strip())
        if match is None:
            raise ValueError(
                f"Invalid GitHub App permission {scope!r}. Use '<permission>:<level>' "
                "with level read, write, or admin, e.g. 'issues:write'."
            )
        permissions[match["name"]] = match["level"]
    return permissions


class GitHubAppProvider(ClientCredentialsOAuthProvider):
    """GitHub App provider that authenticates as an app installation.

    The client ID is the GitHub App client ID (or numeric App ID) and the client
    secret is the app's PEM private key. The token endpoint is either the
    installation's ``/app/installations/{id}/access_tokens`` URL, or the
    ``/app/installations`` URL to use the app's only installation.
    """

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github",
        name="GitHub App",
        description=(
            "Authenticate as a GitHub App installation with short-lived "
            "installation access tokens."
        ),
        setup_instructions=(
            "Create a GitHub App, install it on the organization or repositories "
            "to automate, and generate a private key. Enter the app's client ID "
            "(or App ID) and paste the private key. Leave the token endpoint as "
            "https://api.github.com/app/installations when the app has a single "
            "installation; otherwise set it to "
            "https://api.github.com/app/installations/<installation_id>/access_tokens. "
            "Optionally restrict the token with scopes such as 'issues:write' or "
            "'contents:read'."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation",
        setup_guide_url="https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app",
        troubleshooting_url="https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps",
    )
    # Installation tokens only use the token endpoint. The base provider requires
    # both fields, so the unused authorization endpoint mirrors it.
    default_authorization_endpoint: ClassVar[str | None] = (
        GITHUB_APP_INSTALLATIONS_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = GITHUB_APP_INSTALLATIONS_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused for GitHub App authentication. Keep it matching the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Use https://api.github.com/app/installations when the app has one installation.",
        "Otherwise use https://api.github.com/app/installations/<installation_id>/access_tokens.",
        "For GitHub Enterprise Server, replace https://api.github.com with https://<host>/api/v3.",
    ]

    def _resolve_client_credentials(
        self, client_id: str | None, client_secret: str | None
    ) -> ClientCredentials:
        credentials = super()._resolve_client_credentials(client_id, client_secret)
        if not credentials.client_secret:
            raise ValueError("GitHub App provider requires the app's private key.")
        private_key = normalize_github_app_private_key(credentials.client_secret)
        try:
            load_pem_private_key(private_key.encode(), password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "GitHub App private key must be an unencrypted PEM private key."
            ) from exc
        return ClientCredentials(
            client_id=credentials.client_id.strip(), client_secret=private_key
        )

    def _create_app_jwt(self) -> str:
        if not self.client_secret:
            raise ValueError("GitHub App provider requires the app's private key.")
        now = int(time.time())
        return jwt.encode(
            {
                "iat": now - _JWT_BACKDATE_SECONDS,
                "exp": now + _JWT_LIFETIME_SECONDS,
                "iss": self.client_id,
            },
            self.client_secret,
            algorithm="RS256",
        )

    @staticmethod
    def _headers(app_jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    async def _resolve_access_tokens_url(
        self, client: httpx.AsyncClient, app_jwt: str
    ) -> str:
        parsed = urlparse(self.token_endpoint)
        if _ACCESS_TOKENS_PATH.fullmatch(parsed.path):
            return self.token_endpoint
        if (match := _INSTALLATIONS_PATH.fullmatch(parsed.path)) is None:
            raise ValueError(
                "GitHub App token endpoint must be "
                "<api>/app/installations/<installation_id>/access_tokens or "
                "<api>/app/installations."
            )

        installations_url = urlunparse(
            parsed._replace(path=f"{match['prefix']}/app/installations", query="")
        )
        response = await client.get(
            installations_url,
            headers=self._headers(app_jwt),
            params={"per_page": 100},
        )
        response.raise_for_status()
        installations: list[dict[str, Any]] = response.json()
        if len(installations) != 1:
            accounts = ", ".join(
                f"{(inst.get('account') or {}).get('login', 'unknown')} ({inst.get('id')})"
                for inst in installations
            )
            raise ValueError(
                f"GitHub App has {len(installations)} installations"
                + (f": {accounts}" if accounts else "")
                + ". Set the token endpoint to "
                f"{installations_url}/<installation_id>/access_tokens."
            )
        installation_id = int(installations[0]["id"])
        return urlunparse(
            parsed._replace(
                path=f"{match['prefix']}/app/installations/{installation_id}/access_tokens",
                query="",
            )
        )

    async def get_client_credentials_token(self) -> TokenResponse:
        permissions = parse_github_app_permissions(self.requested_scopes)
        app_jwt = self._create_app_jwt()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            access_tokens_url = await self._resolve_access_tokens_url(client, app_jwt)
            response = await client.post(
                access_tokens_url,
                headers=self._headers(app_jwt),
                json={"permissions": permissions} if permissions else None,
            )
            if response.is_error:
                self.logger.error(
                    "Failed to create GitHub App installation token",
                    provider=self.id,
                    status_code=response.status_code,
                )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("GitHub did not return an installation access token.")

        granted: dict[str, str] = payload.get("permissions") or {}
        self.logger.info(
            "Successfully acquired GitHub App installation token", provider=self.id
        )
        return TokenResponse(
            access_token=SecretStr(token),
            refresh_token=None,
            expires_in=self._compute_expires_in(payload.get("expires_at")),
            scope=" ".join(f"{name}:{level}" for name, level in granted.items()),
            token_type="Bearer",
        )

    @staticmethod
    def _compute_expires_in(expires_at: str | None) -> int:
        if not expires_at:
            return 3600
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return max(int((expiry - datetime.now(UTC)).total_seconds()), 0)
