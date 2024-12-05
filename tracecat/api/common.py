from urllib.parse import urlparse

from fastapi import Request, status
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.repository import safe_url
from tracecat.store.client import get_store
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatException


def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unexpected error",
        exc=exc,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "An unexpected error occurred. Please try again later."},
    )


def bootstrap_role():
    """Role to bootstrap Tracecat services."""
    return Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-api",
    )


async def setup_registry(session: AsyncSession, admin_role: Role):
    logger.info("Setting up base registry repository")
    repos_service = RegistryReposService(session, role=admin_role)
    # Setup Tracecat base repository
    base_origin = DEFAULT_REGISTRY_ORIGIN
    # Check if the base registry repository already exists
    # NOTE: Should we sync the base repo every time?
    if await repos_service.get_repository(base_origin) is None:
        base_repo = await repos_service.create_repository(
            RegistryRepositoryCreate(origin=base_origin)
        )
        logger.info("Created base registry repository", origin=base_origin)
        actions_service = RegistryActionsService(session, role=admin_role)
        await actions_service.sync_actions_from_repository(base_repo)
    else:
        logger.info("Base registry repository already exists", origin=base_origin)

    # Setup custom repository
    custom_origin = CUSTOM_REPOSITORY_ORIGIN
    if await repos_service.get_repository(custom_origin) is None:
        await repos_service.create_repository(
            RegistryRepositoryCreate(origin=custom_origin)
        )
        logger.info("Created custom repository", origin=custom_origin)
    else:
        logger.info("Custom repository already exists", origin=custom_origin)

    # Setup custom remote repository
    if (remote_url := config.TRACECAT__REMOTE_REPOSITORY_URL) is not None:
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


def tracecat_exception_handler(request: Request, exc: TracecatException):
    """Generic exception handler for Tracecat exceptions.

    We can customize exceptions to expose only what should be user facing.
    """
    msg = str(exc)
    logger.error(
        msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"type": type(exc).__name__, "message": msg, "detail": exc.detail},
    )


def custom_generate_unique_id(route: APIRoute):
    if route.tags:
        return f"{route.tags[0]}-{route.name}"
    return route.name


async def setup_oss_models():
    if not (preload_models := config.TRACECAT__PRELOAD_OSS_MODELS):
        return
    from tracecat.llm import preload_ollama_models

    logger.info(
        f"Preloading {len(preload_models)} models",
        models=preload_models,
    )
    await preload_ollama_models(preload_models)
    logger.info("Preloaded models", models=preload_models)


async def setup_store():
    store = get_store()
    try:
        await store.create_bucket("tracecat")
        logger.info("Object store setup complete")
    except Exception as e:
        exc_type = e.__class__.__name__
        if exc_type == "BucketAlreadyOwnedByYou":
            logger.info("Object store already setup")
        else:
            logger.warning("Couldn't set up object store", error=e)
