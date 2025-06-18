from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.providers.github import GitHubOAuthProvider
from tracecat.integrations.providers.google import GoogleOAuthProvider
from tracecat.integrations.providers.microsoft import MicrosoftOAuthProvider
from tracecat.integrations.providers.slack import SlackOAuthProvider

# Provider registry - maps provider ids to their implementations
PROVIDER_REGISTRY: dict[str, type[BaseOauthProvider]] = {
    "microsoft": MicrosoftOAuthProvider,
    "microsoft-teams": MicrosoftOAuthProvider,  # alias for backward compatibility
    "google": GoogleOAuthProvider,
    "github": GitHubOAuthProvider,
    "slack": SlackOAuthProvider,
}
