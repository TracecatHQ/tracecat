"""Slack OAuth provider using authorization code flow for user tokens."""

from typing import Any, ClassVar, cast

from pydantic import SecretStr

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes
from tracecat.integrations.types import TokenResponse

SLACK_AUTHORIZATION_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_SETUP_GUIDE_URL = "https://docs.slack.dev/authentication/installing-with-oauth"
SLACK_API_DOCS_URL = "https://api.slack.com/authentication/oauth-v2"
SLACK_TROUBLESHOOT_URL = "https://api.slack.com/authentication/troubleshooting"


class SlackOAuthProvider(AuthorizationCodeOAuthProvider):
    """Slack OAuth provider using authorization code flow for user-level tokens."""

    id: ClassVar[str] = "slack"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["search:read"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="slack",
        name="Slack (Delegated)",
        description="Slack OAuth provider for user-level access tokens.",
        requires_config=True,
        enabled=True,
        api_docs_url=SLACK_API_DOCS_URL,
        setup_guide_url=SLACK_SETUP_GUIDE_URL,
        troubleshooting_url=SLACK_TROUBLESHOOT_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = SLACK_AUTHORIZATION_URL
    default_token_endpoint: ClassVar[str | None] = SLACK_TOKEN_URL

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        **kwargs: Any,
    ):
        self._user_scopes = scopes or self.scopes.default
        # Slack expects user scopes via the `user_scope` param; avoid sending them as `scope`.
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            scopes=[],
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            **kwargs,
        )
        self.requested_scopes = self._user_scopes

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        params = super()._get_additional_authorize_params()
        params["user_scope"] = " ".join(self._user_scopes)
        return params

    def _build_token_response(self, token: dict[str, Any]) -> TokenResponse:
        authed_user = cast(dict[str, Any] | None, token.get("authed_user")) or {}
        access_token = authed_user.get("access_token") or token.get("access_token")
        if not access_token:
            raise ValueError("Slack token response missing access token")

        refresh_token = authed_user.get("refresh_token") or token.get("refresh_token")
        scope = (
            authed_user.get("scope")
            or token.get("scope")
            or " ".join(self.requested_scopes)
        )
        token_type = (
            authed_user.get("token_type") or token.get("token_type") or "Bearer"
        )
        expires_in = authed_user.get("expires_in") or token.get("expires_in") or 3600

        return TokenResponse(
            access_token=SecretStr(access_token),
            refresh_token=SecretStr(refresh_token) if refresh_token else None,
            expires_in=expires_in,
            scope=scope,
            token_type=token_type,
        )

    async def exchange_code_for_token(
        self, code: str, state: str, code_verifier: str | None = None
    ) -> TokenResponse:
        try:
            token_params = self._get_additional_token_params()
            if code_verifier:
                token_params["code_verifier"] = code_verifier

            token = cast(
                dict[str, Any],
                await self.client.fetch_token(
                    self.token_endpoint,
                    code=code,
                    state=state,
                    **token_params,
                ),
            )
            self.logger.info(
                "Successfully acquired Slack OAuth token",
                provider=self.id,
                used_pkce=code_verifier is not None,
            )
            return self._build_token_response(token)
        except Exception as exc:
            self.logger.error(
                "Error exchanging code for Slack token",
                provider=self.id,
                error=str(exc),
            )
            raise

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        try:
            token = cast(
                dict[str, Any],
                await self.client.refresh_token(
                    self.token_endpoint,
                    refresh_token=refresh_token,
                    **self._get_additional_token_params(),
                ),
            )
            self.logger.info(
                "Successfully refreshed Slack OAuth token",
                provider=self.id,
            )
            return self._build_token_response(token)
        except Exception as exc:
            self.logger.error(
                "Error refreshing Slack token",
                provider=self.id,
                error=str(exc),
            )
            raise
