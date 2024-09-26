import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserOrServiceRole
from tracecat.contexts import ctx_logger
from tracecat.dsl.models import UDFActionInput
from tracecat.logger import logger
from tracecat.registry import executor
from tracecat.registry.manager import RegistryManager
from tracecat.registry.models import (
    CreateRegistryParams,
    RegisteredUDFRead,
    ValidateActionParams,
)

management_router = APIRouter(prefix="/registry")
executor_router = APIRouter(prefix="/registry-executor")


def get_registry_manager() -> RegistryManager:
    # Lifespan for APIRouter is currently not supported
    # https://github.com/fastapi/fastapi/discussions/9664
    registry_manager = RegistryManager()
    registry_manager.get_registry()
    logger.info("Initialized base registry")
    return registry_manager


manager = get_registry_manager()


@management_router.get("", tags=["registry"])
async def list_registries() -> list[str]:
    regs = manager.list_registries()
    logger.info("Listing registries", registries=regs)
    return regs


@management_router.get("/{version}", tags=["registry"])
async def get_registry(version: str) -> list[RegisteredUDFRead]:
    registry = manager.get_registry(version)
    logger.info("Getting registry", version=version, registry=registry)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    return registry.list_actions()


@management_router.post(
    "/{version}", tags=["registry"], status_code=status.HTTP_201_CREATED
)
async def create_registry(version: str, params: CreateRegistryParams):
    try:
        logger.info("Creating registry", version=version, params=params)
        return manager.create_registry(version=version, **params.model_dump())
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@management_router.patch(
    "/{version}", tags=["registry"], status_code=status.HTTP_204_NO_CONTENT
)
async def update_registry(version: str):
    return manager.update_registry(version)


@management_router.delete(
    "/{version}", tags=["registry"], status_code=status.HTTP_204_NO_CONTENT
)
async def delete_registry(version: str):
    return manager.delete_registry(version)


@executor_router.post("/{action_name}", tags=["regstry-executor"])
async def run_action(
    role: WorkspaceUserOrServiceRole,
    action_name: str,
    action_input: UDFActionInput,
) -> Any:
    ref = action_input.task.ref
    act_logger = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(act_logger)

    act_logger.info("Starting action")
    try:
        return await executor.run_action_from_input(input=action_input)
    except Exception as e:
        act_logger.error(f"Error running action {action_name} in executor: {e}")
        raise


@executor_router.post("/{action_name}/validate", tags=["regstry-executor"])
async def validate_action(
    role: WorkspaceUserOrServiceRole,
    action_name: str,
    params: ValidateActionParams,
) -> Any:
    return await asyncio.to_thread(
        executor.validate_action_args,
        action_name=action_name,
        args=params.args,
        registry_version=params.registry_version,
    )
