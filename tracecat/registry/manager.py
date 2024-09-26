from __future__ import annotations

import asyncio

from tracecat_registry import __version__ as REGISTRY_VERSION

from tracecat import config
from tracecat.concurrency import CloudpickleProcessPoolExecutor
from tracecat.logger import logger
from tracecat.registry.models import RegisteredUDF
from tracecat.registry.store import Registry
from tracecat.types.exceptions import RegistryError


class RegistryManager:
    """Singleton class that manages different versions of registries."""

    _instance = None
    _base_version = REGISTRY_VERSION
    _registries: dict[str, Registry] = {}
    _executor: CloudpickleProcessPoolExecutor | None = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls, *args, **kwargs)
            # cls._executor = CloudpickleProcessPoolExecutor()
        return cls._instance

    def __repr__(self) -> str:
        return f"RegistryManager(registries={self._registries})"

    def _is_base_version(self, version: str) -> bool:
        return version == self._base_version

    def get_action(self, action_name: str, version: str | None = None) -> RegisteredUDF:
        version = version or self._base_version
        registry = self.get_registry(version)
        if registry is None:
            raise RegistryError(
                f"Registry {version!r} not found. Available registries: {self.list_registries()}"
            )
        try:
            return registry.get(action_name)
        except KeyError as e:
            raise RegistryError(
                f"Action {action_name!r} not found in registry {version!r}. Available actions: {registry.keys}"
            ) from e

    def get_registry(self, version: str | None = None) -> Registry | None:
        version = version or self._base_version
        registry = self._registries.get(version)
        if registry is None:
            if not self._is_base_version(version):
                # If the version we're looking for is not the current version
                # We need to fetch it from a remote source
                # We're currently just throwing an error
                logger.warning(f"Registry {version} not found")
                return None
            registry = Registry(version)
            registry.init()
            self.add_registry(registry)
        return registry

    def list_registries(self) -> list[str]:
        return list(self._registries.keys())

    def delete_registry(self, version: str):
        if version in self._registries:
            del self._registries[version]

    def create_registry(
        self,
        *,
        version: str,
        name: str | None = None,
        include_base: bool = True,
        include_remote: bool = False,  # Not supported yet
        include_templates: bool = True,
    ):
        if config.TRACECAT__APP_ENV != "development" and not self._is_base_version(
            version
        ):
            raise NotImplementedError(
                "Non-base versioned registries not supported yet."
            )

        registry = Registry(name or version)
        registry.init(
            include_base=include_base,
            include_remote=include_remote,
            include_templates=include_templates,
        )
        self.add_registry(registry)

    def add_registry(self, registry: Registry):
        self._registries[registry.version] = registry

    def update_registry(self, version: str):
        if version not in self._registries:
            logger.warning(f"Registry {version} does not exist, fetching from remote")
            # Do fetching here using uv
        self._registries[version] = Registry(version)

    async def fetch_registry(
        self, *, version: str | None = None, remote_url: str | None = None
    ) -> Registry:
        """This is a work in progress."""
        # Need to get a different registry
        # Install the registry using uv pip install under a different module name
        # Import the registry and return it
        # Add the registry to the manager
        if (version is None) and (remote_url is None):
            raise ValueError("Either registry version or remote_url must be provided")

        if version is not None:
            # Pull a tracecat registry from github
            remote = f"tracecat_registry @ git+https://github.com/TracecatHQ/tracecat@{version}#subdirectory=registry&egg=tracecat_registry"
            try:
                process = await asyncio.create_subprocess_exec(
                    "uv",
                    "pip",
                    "install",
                    remote,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    error_message = stderr.decode().strip()
                    logger.error(f"Failed to install registry: {error_message}")
                    raise RuntimeError(f"Failed to install registry: {error_message}")

                logger.info("Registry installed successfully")
                return Registry(version)
            except Exception as e:
                logger.error(f"Error while fetching registry: {str(e)}")
                raise RuntimeError(f"Error while fetching registry: {str(e)}") from e
        if remote_url is not None:
            return self.fetch_registry_from_url(remote_url)

    @classmethod
    def shutdown(cls):
        logger.info("Shutting down registry manager")
        if cls._executor:
            cls._executor.shutdown()
