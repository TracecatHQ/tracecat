import importlib
from pathlib import Path
from typing import Self

from tracecat.integrations.base import BaseOauthProvider


def load_providers():
    plugins_dir = Path(__file__).parent
    package_name = "tracecat.integrations.providers"
    for file in plugins_dir.glob("*.py"):
        if file.name != "__init__.py":
            module_name = f"{package_name}.{file.stem}"
            importlib.import_module(module_name)


load_providers()


class ProviderRegistry:
    _instance: Self | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._providers: dict[str, type[BaseOauthProvider]] = {
            provider.id: provider for provider in BaseOauthProvider.__subclasses__()
        }

    @classmethod
    def get(cls) -> Self:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_class(self, provider_id: str) -> type[BaseOauthProvider] | None:
        """Get an initialized provider by its ID."""
        return self._providers.get(provider_id)

    def list_providers(self) -> list[str]:
        """List the IDs of all available providers."""
        return list(self._providers.keys())
