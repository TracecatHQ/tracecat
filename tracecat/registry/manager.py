from __future__ import annotations

import asyncio
from typing import cast

from tracecat_registry import __version__ as REGISTRY_VERSION

from tracecat.concurrency import CloudpickleProcessPoolExecutor
from tracecat.logger import logger
from tracecat.registry.models import ArgsT, RegisteredUDF, RunActionParams
from tracecat.registry.store import Registry


class RegistryManager:
    """Singleton class that manages different versions of registries."""

    _instance = None
    _base_version = REGISTRY_VERSION
    _registries: dict[str, Registry] = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._executor = CloudpickleProcessPoolExecutor()
        return cls._instance

    def __repr__(self) -> str:
        return f"RegistryManager(registries={self._registries})"

    def get_action(
        self, action_name: str, version: str = REGISTRY_VERSION
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
        version: str = REGISTRY_VERSION,
    ):
        """Decides how to run the action based on the type of UDF."""
        udf = self.get_action(action_name, version)
        validated_args = udf.validate_args(**params.args)
        if udf.metadata.get("is_template"):
            kwargs = cast(
                ArgsT, {"args": validated_args, "base_context": params.context or {}}
            )
        else:
            kwargs = validated_args
        logger.warning("Running action in manager", kwargs=kwargs)
        try:
            if udf.is_async:
                logger.info("Running UDF async")
                return await udf.fn(**params.args)
            logger.info("Running udf in sync executor pool")
            return asyncio.to_thread(udf.fn, **params.args)
        except Exception as e:
            logger.error(f"Error running UDF {udf.key!r}: {e}")
            raise

    def get_registry(self, version: str = REGISTRY_VERSION) -> Registry:
        registry = self._registries.get(version)
        if registry is None:
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
