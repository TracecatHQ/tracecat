"""Test suite helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from slugify import slugify

from tracecat.dsl.models import TaskResult
from tracecat.ee.store.models import as_object_ref
from tracecat.ee.store.object_store import ObjectStore
from tracecat.identifiers.workflow import EXEC_ID_PREFIX, WorkflowUUID
from tracecat.registry.actions.models import TemplateAction


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )


TEST_WF_ID = WorkflowUUID(int=0)


def generate_test_exec_id(name: str) -> str:
    return TEST_WF_ID.short() + f"/{EXEC_ID_PREFIX}" + slugify(name, separator="_")


def glob_file_paths(dir_path: Path, file_ext: str) -> list[Path]:
    """Glob all files with the given extension in the given directory."""
    return list(dir_path.glob(f"*.{file_ext}"))


def load_yaml_template_action(file_path: Path) -> TemplateAction:
    with open(file_path) as f:
        definition = yaml.safe_load(f)

    action = TemplateAction.model_validate(definition)
    return action


async def resolve_task_result(task_result: TaskResult) -> Any:
    """Resolve a task result to a value."""
    match task_result:
        case {"result_typename": "tracecat/object-ref", "result": result}:
            if obj_ref := as_object_ref(result):
                return await ObjectStore.get().get_object(obj_ref)
            raise ValueError("Invalid object ref")
        case _:
            return task_result["result"]
