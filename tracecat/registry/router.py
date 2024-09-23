from typing import Any

from fastapi import APIRouter, Query, status

from tracecat.logger import logger
from tracecat.registry.manager import RegistryManager
from tracecat.registry.models import RegisteredUDFRead, RunActionParams

router = APIRouter(prefix="/registry")


def get_registry_manager() -> RegistryManager:
    # Lifespan for APIRouter is currently not supported
    # https://github.com/fastapi/fastapi/discussions/9664
    registry_manager = RegistryManager()
    registry_manager.get_registry()
    return registry_manager


manager = get_registry_manager()


@router.get("", tags=["registry"])
async def list_registries() -> list[str]:
    return manager.list_registries()


@router.get("/{version}", tags=["registry"])
async def get_registry(version: str) -> list[RegisteredUDFRead]:
    registry = manager.get_registry(version)
    return registry.list_actions()


@router.post("/{version}", tags=["registry"], status_code=status.HTTP_201_CREATED)
async def create_registry(version: str):
    return manager.create_registry(version)


@router.patch("/{version}", tags=["registry"], status_code=status.HTTP_204_NO_CONTENT)
async def update_registry(version: str):
    return manager.update_registry(version)


@router.delete("/{version}", tags=["registry"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry(version: str):
    return manager.delete_registry(version)


@router.post("/executor/{action_name}", tags=["regstry-executor"])
async def run_action(
    action_name: str,
    params: RunActionParams,
    version: str | None = Query(default=None),
) -> Any:
    logger.info(f"Running action {action_name} with params {params}")
    version = version or manager.get_registry().version
    result = await manager.run_action(
        action_name=action_name, version=version, params=params
    )
    return result


# @router.get("/actions", tags=["regstry-actions"])
# async def list_actions():
#     return {"message": "Hello world. I am the registry."}


# @router.get("/actions/{action_name}", tags=["regstry-actions"])
# async def get_action(action_name: str):
#     return {"message": f"Hello world. I am the registry. {action_name}"}


# @router.post("/actions/{action_name}", tags=["regstry-actions"])
# async def create_action(action_name: str):
#     return {"message": f"Hello world. I am the registry. {action_name}"}


# @router.patch("/actions/{action_name}", tags=["regstry-actions"])
# async def update_action(action_name: str):
#     return {"message": f"Hello world. I am the registry. {action_name}"}


# @router.delete("/actions/{action_name}", tags=["regstry-actions"])
# async def delete_action(action_name: str):
#     return {"message": f"Hello world. I am the registry. {action_name}"}
