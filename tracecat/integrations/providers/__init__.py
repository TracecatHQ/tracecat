from tracecat.integrations.base import BaseOauthProvider

# Import other providers here so they are loaded into memory and discoverable
# e.g. from tracecat.integrations.providers.google import GoogleOAuthProvider

# Discover all subclasses of BaseOauthProvider and create instances
_providers: dict[str, type[BaseOauthProvider]] = {
    provider.id: provider for provider in BaseOauthProvider.__subclasses__()
}


def get_provider(provider_id: str) -> type[BaseOauthProvider] | None:
    """Get an initialized provider by its ID."""
    return _providers.get(provider_id)


def list_providers() -> list[str]:
    """List the IDs of all available providers."""
    return list(_providers.keys())
