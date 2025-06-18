from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.providers.microsoft import MicrosoftOAuthProvider

# Provider registry - maps provider ids to their implementations
PROVIDER_REGISTRY: dict[str, type[BaseOauthProvider]] = {
    "microsoft": MicrosoftOAuthProvider,
    "microsoft-teams": MicrosoftOAuthProvider,  # alias for backward compatibility
}
