from __future__ import annotations

import asyncio
import json
from typing import Literal, overload

from tracecat_registry import REGISTRY_VERSION

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.actions.models import BoundRegistryAction
from tracecat.registry.repository import Repository
from tracecat.types.exceptions import RegistryError, RegistryNotFound


class RegistryManager:
    """Singleton class that manages different versions of registries.

    This is kept as a mapping of versions to registries.

    Types of registries
    -------------------
    1. Base versions: These are the versions that are bundled with tracecat. Includes actions and templates.
    e.g. base versions: base-0.1.0, base-0.2.0

    2. Custom versions: These are the versions that are installed by the user.
    Can have different origins. Currently supported origin is a git repository.
    e.g. custom-udfs-0.1.0, custom-udfs-0.2.0

    Each registry will load from its repective `origin`.
    Base registries will load from `tracecat_registry` package.
    If a specific version of base registry is not installed, we should pull it from GH. (todo)
    e.g. Custom registry stored in GH will load from `git+https://github.com/user/repo.git`

    Responsibilities
    ----------------
    1. Maintain a mapping of version to registry
    2. Provide an interface to list actions, get action, create template
    3. Interface to list registries, get registry, create registry, update registry, delete registry
    4. Manage the registry lifecycle
    5. Manage template action lifecycle
    """

    _instance = None
    _base_version: str = REGISTRY_VERSION  # Mutable
    _custom_version: str | None = None
    _repos: dict[str, Repository] = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __repr__(self) -> str:
        return f"RegistryManager(registries={json.dumps(self._repos, indent=2)})"

    def _is_base_version(self, version: str) -> bool:
        return version == self._base_version

    def set_version(self, version: str):
        self._curr_version = version

    """Registry Actions"""

    def get_action(
        self, *, action_name: str, version: str | None = None
    ) -> BoundRegistryAction:
        version = version or self._base_version
        registry = self.get_repository(version, raise_on_missing=True)
        try:
            return registry.get(action_name)
        except KeyError as e:
            raise RegistryError(
                f"Action {action_name!r} not found in registry {version!r}. Available actions: {registry.keys}"
            ) from e

    """Registry Management"""

    @overload
    def get_repository(
        self, version: str | None = None, raise_on_missing: Literal[False] = False
    ) -> Repository | None: ...

    @overload
    def get_repository(
        self, version: str | None = None, raise_on_missing: Literal[True] = True
    ) -> Repository: ...

    def get_repository(
        self, version: str | None = None, raise_on_missing: bool = False
    ) -> Repository | None:
        version = version or self._base_version
        registry = self._repos.get(version)
        if registry is None:
            if not self._is_base_version(version):
                # If the version we're looking for is not the current version
                # We need to fetch it from a remote source
                # We're currently just throwing an error or returning None
                logger.warning(f"Registry {version} not found")
                if raise_on_missing:
                    raise RegistryNotFound(
                        f"Registry {version!r} not found. Available registries: {self.list_repositories()}"
                    )
                return None
            # We're looking for the base version
            registry = Repository(version)
            registry.init()
            self.add_repository(registry)
        return registry

    def list_repositories(self) -> list[str]:
        return list(self._repos.keys())

    def delete_repository(self, version: str):
        if version in self._repos:
            del self._repos[version]

    def create_repository(
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
                "Non-base versioned repositories not supported yet."
            )

        repo = Repository(name or version)
        repo.init(
            include_base=include_base,
            include_remote=include_remote,
            include_templates=include_templates,
        )
        self.add_repository(repo)

    def add_repository(self, repo: Repository):
        self._repos[repo.version] = repo

    def update_repository(self, version: str):
        if version not in self._repos:
            logger.warning(f"Registry {version} does not exist, fetching from remote")
            # Do fetching here using uv
        self._repos[version] = Repository(version)

    def ensure_base_repository(self):
        if self._base_version not in self._repos:
            self.create_repository(version=self._base_version)

    async def fetch_repository(
        self, *, version: str | None = None, remote_url: str | None = None
    ) -> Repository:
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
                return Repository(version)
            except Exception as e:
                logger.error(f"Error while fetching registry: {str(e)}")
                raise RuntimeError(f"Error while fetching registry: {str(e)}") from e
        if remote_url is not None:
            return self.fetch_registry_from_url(remote_url)
