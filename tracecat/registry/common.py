from typing import cast
from urllib.parse import urlparse

from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.logger import logger
from tracecat.parse import safe_url
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting
from tracecat.types.auth import Role


async def reload_registry(session: AsyncSession, role: Role):
    logger.info("Setting up base registry repository")
    repos_service = RegistryReposService(session, role=role)
    # Setup Tracecat base repository
    base_origin = DEFAULT_REGISTRY_ORIGIN
    # Check if the base registry repository already exists
    # NOTE: Should we sync the base repo every time?
    if await repos_service.get_repository(base_origin) is None:
        base_repo = await repos_service.create_repository(
            RegistryRepositoryCreate(origin=base_origin)
        )
        logger.info("Created base registry repository", origin=base_origin)
        actions_service = RegistryActionsService(session, role=role)
        await actions_service.sync_actions_from_repository(base_repo)
    else:
        logger.info("Base registry repository already exists", origin=base_origin)

    # Setup custom repository
    # This is where custom template actions are created and stored
    custom_origin = CUSTOM_REPOSITORY_ORIGIN
    if await repos_service.get_repository(custom_origin) is None:
        await repos_service.create_repository(
            RegistryRepositoryCreate(origin=custom_origin)
        )
        logger.info("Created custom repository", origin=custom_origin)
    else:
        logger.info("Custom repository already exists", origin=custom_origin)

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
            await repos_service.create_repository(
                RegistryRepositoryCreate(origin=local_origin)
            )
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
            await repos_service.create_repository(
                RegistryRepositoryCreate(origin=cleaned_url)
            )
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
