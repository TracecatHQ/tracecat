from typing import cast
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.logger import logger
from tracecat.managed_resources.guard import ManagedResourceLockedError
from tracecat.parse import safe_url
from tracecat.registry.constants import DEFAULT_LOCAL_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement


async def ensure_org_repositories(session: AsyncSession, role: Role) -> None:
    """Ensure org-scoped repositories exist based on configuration.

    This creates repository entries for:
    - Local repository (if enabled via config)
    - Custom remote repository (if configured via org settings)

    Note: Platform registry (base Tracecat registry) is NOT handled here -
    it's platform-scoped and handled by `sync_platform_registry_on_startup()` separately.
    """
    remote_url = cast(
        str | None,
        await get_setting(
            "git_repo_url",
            role=role,
        ),
    )
    needs_custom_registry = config.TRACECAT__LOCAL_REPOSITORY_ENABLED or bool(
        remote_url
    )
    if needs_custom_registry:
        if role.organization_id is None:
            logger.warning(
                "Skipping custom repository setup due to missing organization context"
            )
            return
        entitled = await is_org_entitled(
            session, role.organization_id, Entitlement.CUSTOM_REGISTRY
        )
        if not entitled:
            logger.info(
                "Skipping custom repository setup because entitlement is disabled",
                organization_id=role.organization_id,
                entitlement=Entitlement.CUSTOM_REGISTRY.value,
            )
            return

    repos_service = RegistryReposService(session, role=role)

    # Setup local repository
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        if not config.TRACECAT__LOCAL_REPOSITORY_PATH:
            raise ValueError("Local repository path is not set")
        logger.info(
            "Ensuring local registry repository exists",
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
                await session.rollback()
            else:
                logger.info("Created local repository", origin=local_origin)

    # Setup custom remote repository
    if remote_url:
        parsed_url = urlparse(remote_url)
        logger.info("Ensuring remote registry repository exists", url=parsed_url)

        cleaned_url = safe_url(remote_url)
        if await repos_service.get_repository(cleaned_url) is None:
            try:
                await repos_service.create_repository(
                    RegistryRepositoryCreate(origin=cleaned_url)
                )
            except Exception as e:
                logger.error("Error creating remote registry repository", error=e)
                await session.rollback()
            else:
                logger.info("Created remote registry repository", url=cleaned_url)


async def remove_replaced_custom_repository(
    session: AsyncSession,
    role: Role,
    *,
    previous_url: str | None,
    new_url: str | None,
) -> None:
    """Delete the custom repository of a Git URL that ``new_url`` replaced.

    Clearing the URL keeps the repository; it can be removed explicitly.
    """
    if not previous_url or not new_url:
        return
    previous_origin = safe_url(previous_url)
    if previous_origin == safe_url(new_url):
        return
    if not has_scope(role.scopes or frozenset(), "org:registry:delete"):
        logger.info(
            "Keeping replaced registry repository without delete scope",
            origin=previous_origin,
        )
        return
    repos_service = RegistryReposService(session, role=role)
    repository = await repos_service.get_repository(previous_origin)
    if repository is None:
        return
    try:
        await repos_service.delete_repository(repository)
    except ManagedResourceLockedError:
        logger.info(
            "Keeping replaced registry repository managed by a package",
            origin=previous_origin,
        )
        return
    logger.info("Deleted replaced registry repository", origin=previous_origin)
