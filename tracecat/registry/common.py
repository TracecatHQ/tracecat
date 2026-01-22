from typing import cast
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.logger import logger
from tracecat.parse import safe_url
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.settings.service import get_setting


async def reload_registry(session: AsyncSession, role: Role) -> None:
    logger.info("Setting up base registry repository")
    repos_service = RegistryReposService(session, role=role)

    # Setup Tracecat base repository using platform-scoped services
    # The base registry is shared across all organizations and should be
    # stored in platform_registry_* tables, not org-scoped registry_* tables
    platform_repos_service = PlatformRegistryReposService(session, role=role)
    platform_sync_service = PlatformRegistrySyncService(session, role=role)

    base_origin = DEFAULT_REGISTRY_ORIGIN
    # Check if the base registry repository already exists in platform tables
    if await platform_repos_service.get_repository(base_origin) is None:
        try:
            base_repo = await platform_repos_service.create_repository(
                RegistryRepositoryCreate(origin=base_origin)
            )
        except Exception as e:
            logger.error("Error creating base registry repository", error=e)
        else:
            logger.info("Created base registry repository", origin=base_origin)
            await platform_sync_service.sync_repository_v2(base_repo)
    else:
        logger.info("Base registry repository already exists", origin=base_origin)

    # Setup local repository
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        if not config.TRACECAT__LOCAL_REPOSITORY_PATH:
            raise ValueError("Local repository path is not set")
        logger.info(
            "Setting up local registry repository",
            path=config.TRACECAT__LOCAL_REPOSITORY_PATH,
        )
        local_origin = DEFAULT_LOCAL_REGISTRY_ORIGIN
        if await repos_service.get_repository(local_origin) is None:
            try:
                await repos_service.create_repository(
                    RegistryRepositoryCreate(origin=local_origin)
                )
            except Exception as e:
                logger.error("Error creating local registry repository", error=e)
            else:
                logger.info("Created local repository", origin=local_origin)
        else:
            logger.info("Local repository already exists", origin=local_origin)

    # Setup custom remote repository
    if maybe_remote_url := await get_setting(
        "git_repo_url",
        role=role,
    ):
        remote_url = cast(str, maybe_remote_url)
        parsed_url = urlparse(remote_url)
        logger.info("Setting up remote registry repository", url=parsed_url)
        # Create it if it doesn't exist

        cleaned_url = safe_url(remote_url)
        # Repo doesn't exist
        if await repos_service.get_repository(cleaned_url) is None:
            # Create it
            try:
                await repos_service.create_repository(
                    RegistryRepositoryCreate(origin=cleaned_url)
                )
            except Exception as e:
                logger.error("Error creating remote registry repository", error=e)
            else:
                logger.info("Created remote registry repository", url=cleaned_url)
        else:
            logger.info("Remote registry repository already exists", url=cleaned_url)
        # Load remote repository
    else:
        logger.info("Remote registry repository not set, skipping")

    repos = await repos_service.list_repositories()
    logger.info(
        "Found registry repositories",
        n=len(repos),
        repos=[repo.origin for repo in repos],
    )
