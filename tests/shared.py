"""Test suite helpers."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from typing import Any, TypeGuard

import httpx
import yaml
from pydantic import ValidationError

from tracecat.identifiers.action import ref
from tracecat.identifiers.workflow import EXEC_ID_PREFIX, WorkflowUUID
from tracecat.registry.actions.schemas import TemplateAction
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    StoredObject,
    StoredObjectValidator,
    get_object_storage,
)


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )


TEST_WF_ID = WorkflowUUID(int=0)


def generate_test_exec_id(name: str) -> str:
    return TEST_WF_ID.short() + f"/{EXEC_ID_PREFIX}" + ref(name)


def glob_file_paths(dir_path: Path, file_ext: str) -> list[Path]:
    """Glob all files with the given extension in the given directory."""
    return list(dir_path.glob(f"*.{file_ext}"))


def load_yaml_template_action(file_path: Path) -> TemplateAction:
    with open(file_path) as f:
        definition = yaml.safe_load(f)

    action = TemplateAction.model_validate(definition)
    return action


def get_package_path(module: ModuleType) -> Path:
    """Get the path to a package module.

    Args:
        module: The module object to get the path for

    Returns:
        Path to the module directory
    """
    if not module.__file__:
        raise ValueError("Module has no __file__ attribute")
    return Path(module.__file__).parent


def is_externalized(obj: Any) -> TypeGuard[ExternalObject | CollectionObject]:
    try:
        stored = StoredObjectValidator.validate_python(obj)
        return stored.type in ("external", "collection")
    except ValidationError:
        return False


async def to_data(obj: StoredObject) -> Any:
    validated = StoredObjectValidator.validate_python(obj)
    match validated:
        case InlineObject():
            return validated.data
        case ExternalObject() | CollectionObject():
            storage = get_object_storage()
            return await storage.retrieve(validated)
        case _:
            raise TypeError(f"Expected StoredObject, got {type(validated).__name__}")


async def to_inline(obj: StoredObject) -> InlineObject:
    return InlineObject(data=await to_data(obj))
