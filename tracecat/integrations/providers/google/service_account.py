"""Google service account OAuth provider."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, ClassVar

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from pydantic import SecretStr

from tracecat.integrations.providers.base import (
    ServiceAccountOAuthProvider,
    validate_oauth_endpoint,
)
from tracecat.integrations.providers.google.common import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    get_google_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes
from tracecat.integrations.types import TokenResponse
from tracecat.logger import logger

__all__ = [
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_URL",
    "GoogleServiceAccountOAuthProvider",
]

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

DOMAIN_WIDE_DELEGATION_ERROR = (
    "Google rejected the domain-wide delegation grant (unauthorized_client). "
    "The scopes configured on this integration must match exactly the scopes "
    "delegated to this service account's client ID in the Admin console "
    "(Security > Access and data control > API controls > Domain-wide delegation)."
)


def _is_unauthorized_client(exc: GoogleAuthError) -> bool:
    """Return True for Google's structured ``unauthorized_client`` refresh error."""
    if not isinstance(exc, RefreshError) or len(exc.args) < 2:
        return False
    details = exc.args[1]
    return isinstance(details, dict) and details.get("error") == "unauthorized_client"


class GoogleServiceAccountOAuthProvider(ServiceAccountOAuthProvider):
    """Google OAuth provider using service account credentials."""

    id: ClassVar[str] = "google"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=DEFAULT_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google",
        name="Google Cloud",
        description=(
            "Authenticate to Google Cloud APIs with a service account JSON key."
        ),
        api_docs_url="https://cloud.google.com/docs/authentication",
        setup_guide_url="https://developers.google.com/identity/protocols/oauth2/service-account",
        troubleshooting_url="https://cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys",
    )
    default_authorization_endpoint: ClassVar[str | None] = GOOGLE_AUTH_URL
    default_token_endpoint: ClassVar[str | None] = GOOGLE_TOKEN_URL

    def __init__(
        self,
        *,
        subject: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(subject=subject, **kwargs)

    def _load_service_account_info(self, client_secret: str | None) -> dict[str, Any]:
        info = super()._load_service_account_info(client_secret)

        account_type = info.get("type")
        if account_type != "service_account":
            raise ValueError(
                "Google credentials must be a service account key (type 'service_account')."
            )
        if "private_key" not in info:
            raise ValueError("Service account JSON must include a 'private_key'.")

        token_uri = info.get("token_uri")
        if token_uri and token_uri != self.default_token_endpoint:
            validate_oauth_endpoint(token_uri)
            logger.debug(
                "Overriding token endpoint from service account JSON",
                configured=token_uri,
            )
            self._token_endpoint = token_uri

        return info

    def _derive_client_id(
        self, info: dict[str, Any], configured_client_id: str | None
    ) -> str:
        client_email = info.get("client_email")
        if client_email and isinstance(client_email, str) and client_email.strip():
            return client_email.strip()

        if configured_client_id and configured_client_id.strip():
            return configured_client_id.strip()

        raise ValueError(
            "Google service account JSON must include 'client_email' or you must provide "
            "the service account email as the Client ID."
        )

    def _extract_subject(self, info: dict[str, Any]) -> str | None:
        subject = info.pop("subject", None)
        if subject is None:
            return None
        return str(subject).strip() or None

    async def get_client_credentials_token(self) -> TokenResponse:
        if not self.service_account_info:
            raise ValueError(
                "Service account configuration is missing. Ensure the JSON key is saved."
            )

        if not self.requested_scopes:
            raise ValueError(
                "Google service account provider requires at least one scope."
            )

        credentials = service_account.Credentials.from_service_account_info(
            self.service_account_info, scopes=self.requested_scopes
        )

        subject = self.service_account_subject
        if subject:
            credentials = credentials.with_subject(subject)

        try:
            await asyncio.to_thread(credentials.refresh, Request())
        except GoogleAuthError as exc:
            self.logger.error(
                "Failed to refresh Google service account credentials",
                provider=self.id,
                error=str(exc),
            )
            if _is_unauthorized_client(exc):
                raise ValueError(DOMAIN_WIDE_DELEGATION_ERROR) from exc
            raise

        if not credentials.token:
            raise ValueError("Google did not return an access token.")

        expires_in = self._compute_expires_in(credentials.expiry)

        self.logger.info(
            "Successfully acquired Google service account access token",
            provider=self.id,
            subject=subject,
            scopes=self.requested_scopes,
        )

        scope_str = " ".join(self.requested_scopes)

        return TokenResponse(
            access_token=SecretStr(credentials.token),
            refresh_token=None,
            expires_in=expires_in,
            scope=scope_str,
            token_type="Bearer",
        )

    @staticmethod
    def _compute_expires_in(expiry: datetime | None) -> int:
        if expiry is None:
            return 3600

        now = datetime.now(UTC)
        if expiry.tzinfo is None:
            expiry_ts = expiry.replace(tzinfo=UTC)
        else:
            expiry_ts = expiry.astimezone(UTC)
        delta = int((expiry_ts - now).total_seconds())
        return max(delta, 0)
