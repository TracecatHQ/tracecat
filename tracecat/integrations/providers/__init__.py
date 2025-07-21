import importlib
from pathlib import Path
from typing import Self

from tracecat.integrations.models import ProviderKey
from tracecat.integrations.providers.base import BaseOAuthProvider


def load_providers():
    plugins_dir = Path(__file__).parent
    package_name = "tracecat.integrations.providers"
    for file in plugins_dir.glob("*.py"):
        if file.name != "__init__.py":
            module_name = f"{package_name}.{file.stem}"
            importlib.import_module(module_name)


load_providers()


def _collect_subclasses(
    cls: type[BaseOAuthProvider],
) -> list[type[BaseOAuthProvider]]:
    """Recursively collect all subclasses of the given class."""
    subclasses = []
    for subclass in cls.__subclasses__():
        if hasattr(subclass, "id"):
            subclasses.append(subclass)
        subclasses.extend(_collect_subclasses(subclass))
    return subclasses


class ProviderRegistry:
    _instance: Self | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._providers: dict[ProviderKey, type[BaseOAuthProvider]] = {}

        all_providers = _collect_subclasses(BaseOAuthProvider)
        for provider in all_providers:
            if not provider._include_in_registry:
                continue
            key = ProviderKey(id=provider.id, grant_type=provider.grant_type)
            if key in self._providers:
                raise ValueError(
                    f"Duplicate provider ID: {provider.id} with grant type: {provider.grant_type}"
                )
            self._providers[key] = provider

    @classmethod
    def get(cls) -> Self:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_class(self, provider_key: ProviderKey) -> type[BaseOAuthProvider] | None:
        """Get an initialized provider by its ID."""
        return self._providers.get(provider_key)

    @property
    def providers(self) -> list[type[BaseOAuthProvider]]:
        """List all available providers."""
        return list(self._providers.values())
