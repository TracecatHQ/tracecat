import asyncio

from tracecat import __version__ as TRACECAT_VERSION
from tracecat.logger import logger
from tracecat.registry.executor import CloudpickleProcessPoolExecutor
from tracecat.registry.models import RunActionParams
from tracecat.registry.store import Registry
from tracecat.registry.udfs import RegisteredUDF


class RegistryManager:
    """Singleton class that manages different versions of registries."""

    _instance = None
    _base_version = TRACECAT_VERSION
    _registries: dict[str, Registry] = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._executor = CloudpickleProcessPoolExecutor()
        return cls._instance

    def __repr__(self) -> str:
        return f"RegistryManager(registries={self._registries})"

    def get_action(
        self, action_name: str, version: str = TRACECAT_VERSION
    ) -> RegisteredUDF:
        registry = self.get_registry(version)
        try:
            return registry.get(action_name)
        except KeyError as e:
            raise ValueError(
                f"Action {action_name} not found in registry {version}"
            ) from e

    async def run_action(
        self,
        action_name: str,
        params: RunActionParams,
        version: str = TRACECAT_VERSION,
    ):
        udf = self.get_action(action_name, version)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, udf.run_sync, params.args, params.context
        )

    def get_registry(self, version: str = TRACECAT_VERSION) -> Registry:
        if (registry := self._registries.get(version)) is None:
            if version != self._base_version:
                raise ValueError(f"Registry {version} not found")
            registry = Registry(version)
            registry.init()
            self.add_registry(registry)
        return registry

    def list_registries(self) -> list[str]:
        return list(self._registries.keys())

    def delete_registry(self, version: str):
        if version in self._registries:
            del self._registries[version]

    def create_registry(self, version: str, registry: Registry):
        if version in self._registries:
            raise ValueError(f"Registry {version} already exists")
        # Need to get a different registry
        self._registries[version] = registry

    def add_registry(self, registry: Registry):
        self._registries[registry.version] = registry

    def update_registry(self, version: str):
        if version not in self._registries:
            logger.warning(f"Registry {version} does not exist, fetching from remote")
            # Do fetching here using uv
        self._registries[version] = Registry(version)

    def fetch_registry(self, version: str) -> Registry:
        # Need to get a different registry
        # Install the registry using uv pip install under a different module name
        # Import the registry and return it
        # Add the registry to the manager
        pass

    @classmethod
    def shutdown(cls):
        logger.info("Shutting down registry manager")
        cls._executor.shutdown()


if __name__ == "__main__":
    from tracecat.registry.store import Registry, registry

    # Default version
    registry.init()
    print(registry.version)
    manager = RegistryManager()
    manager.add_registry(registry)
    print(manager.get_registry(registry.version))
