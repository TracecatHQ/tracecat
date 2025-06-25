"""Custom OAuth provider for non-standard OAuth2 implementations."""

import os
from typing import Any, ClassVar

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.models import ProviderCategory, ProviderMetadata


class CustomOAuthProvider(BaseOAuthProvider):
    """Custom OAuth provider that can be configured via environment variables."""

    id: ClassVar[str] = "custom"
    default_scopes: ClassVar[list[str]] = []
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="custom",
        name="Custom",
        description="Configure your own OAuth2 provider with custom endpoints and parameters",
        categories=[ProviderCategory.OTHER],
        features=[
            "Flexible endpoint configuration",
            "PKCE support",
            "Custom parameters",
            "Multi-provider support",
        ],
        setup_steps=[
            "Define environment variables for your OAuth provider endpoints",
            "Set CUSTOM_AUTHORIZATION_ENDPOINT to your provider's auth URL",
            "Set CUSTOM_TOKEN_ENDPOINT to your provider's token URL",
            "Add CUSTOM_CLIENT_ID and CUSTOM_CLIENT_SECRET from your provider",
            "Optionally set CUSTOM_USE_PKCE=true for PKCE-enabled providers",
            "Configure additional parameters with CUSTOM_PARAM_* variables",
        ],
        enabled=False,
    )

    def __init__(self, provider_id: str | None = None):
        """Initialize a custom OAuth provider.

        Args:
            provider_id: Optional provider ID to use instead of "custom".
                        This allows multiple custom providers.
        """
        # Store instance-specific provider_id
        self._provider_id = provider_id or "custom"

        # Get endpoints from environment
        env_prefix = self._provider_id.upper().replace("-", "_")

        authorization_endpoint = os.getenv(f"{env_prefix}_AUTHORIZATION_ENDPOINT")
        token_endpoint = os.getenv(f"{env_prefix}_TOKEN_ENDPOINT")

        if not authorization_endpoint or not token_endpoint:
            raise ValueError(
                f"Custom OAuth provider '{self._provider_id}' endpoints not configured. "
                f"Set {env_prefix}_AUTHORIZATION_ENDPOINT and {env_prefix}_TOKEN_ENDPOINT environment variables."
            )

        # For custom providers, we'll override the class variables dynamically
        # Create a new class to avoid modifying the base CustomOAuthProvider
        self.__class__ = type(
            f"{self._provider_id.title().replace('-', '')}OAuthProvider",
            (CustomOAuthProvider,),
            {
                "id": self._provider_id,
                "authorization_endpoint": authorization_endpoint,
                "token_endpoint": token_endpoint,
            },
        )

        # Get optional configuration
        self.use_pkce = os.getenv(f"{env_prefix}_USE_PKCE", "false").lower() == "true"
        self.additional_params = {}

        # Parse additional parameters from environment
        for key, value in os.environ.items():
            if key.startswith(f"{env_prefix}_PARAM_"):
                param_name = key.replace(f"{env_prefix}_PARAM_", "").lower()
                self.additional_params[param_name] = value

        # Initialize parent class
        super().__init__()

    def _use_pkce(self) -> bool:
        """Check if PKCE should be used."""
        return self.use_pkce

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Get additional authorization parameters."""
        return self.additional_params.copy()

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Get additional token parameters."""
        return self.additional_params.copy()


def create_custom_provider(provider_id: str) -> type[CustomOAuthProvider]:
    """Factory function to create a custom provider class with a specific ID."""

    class DynamicCustomProvider(CustomOAuthProvider):
        id = provider_id

        def __init__(self):
            super().__init__(provider_id=provider_id)

    DynamicCustomProvider.__name__ = (
        f"{provider_id.title().replace('-', '')}OAuthProvider"
    )
    return DynamicCustomProvider
