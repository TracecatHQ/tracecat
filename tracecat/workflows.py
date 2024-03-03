import asyncio
import logging
import random
from collections.abc import Iterable
from enum import StrEnum, auto
from functools import cached_property
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, root_validator

from tracecat.actions import (
    Action,
    ActionResult,
    ActionSubclass,
    ActionTrail,
    run_action,
)
from tracecat.logger import standard_logger

LOGGER = standard_logger(__name__)


class Workflow(BaseModel):
    """Configuration for a workflow.

    This is different from a workflow run, which is a specific instance of a workflow.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    adj_list: dict[str, list[str]]
    action_map: dict[str, ActionSubclass]

    @cached_property
    def action_dependencies(self) -> dict[str, set[str]]:
        """Return a mapping of action IDs to their dependencies."""
        deps: dict[str, set[str]] = {k: set() for k in self.adj_list.keys()}

        for dependency, actions in self.adj_list.items():
            for action in actions:
                deps[action].add(dependency)
        return deps

    @root_validator(pre=True)
    def parse_actions(cls, values: Any) -> Any:
        actions = values.get("action_map", {})
        values["action_map"] = {k: Action.from_dict(v) for k, v in actions.items()}
        return values


class ActionRun(BaseModel):
    """A run of an action to be executed as part of a workflow run."""

    run_id: str
    action_id: str


class ActionRunStatus(StrEnum):
    """Status of an action run."""

    QUEUED = auto()
    PENDING = auto()
    RUNNING = auto()
    FAILURE = auto()
    SUCCESS = auto()


def _get_dependencies_results(
    dependencies: Iterable[str], action_result_store: dict[str, ActionTrail]
) -> dict[str, ActionResult]:
    """Return a combined trail of the execution results of the dependencies.

    The keys are the action IDs and the values are the results of the actions.
    """
    combined_trail: dict[str, ActionResult] = {}
    for dep in dependencies:
        past_action_result = action_result_store[dep]
        combined_trail |= past_action_result
    return combined_trail


async def _wait_for_dependencies(
    dependencies: Iterable[str], task_status: dict[str, ActionRunStatus]
) -> None:
    while not all(task_status.get(d) == ActionRunStatus.SUCCESS for d in dependencies):
        await asyncio.sleep(random.uniform(0, 1))


async def execute_action_run(
    workflow_id: str,
    run_id: str,
    action: Action,
    adj_list: dict[str, list[str]],
    ready_tasks: asyncio.Queue[ActionRun],
    running_tasks_store: dict[str, asyncio.Task[None]],
    action_result_store: dict[str, ActionTrail],
    task_status_store: dict[str, ActionRunStatus],
    dependencies: Iterable[str],
    pending_timeout: float | None = None,
    logger: logging.Logger | None = None,
) -> None:
    logger = logger or LOGGER
    logger.debug(f"Action {action.id} waiting for dependencies {dependencies}.")
    try:
        await asyncio.wait_for(
            _wait_for_dependencies(dependencies, task_status_store),
            timeout=pending_timeout,
        )

        action_trail = _get_dependencies_results(dependencies, action_result_store)

        logger.debug(f"Running action {action.id!r}. Trail {action_trail.keys()}.")
        task_status_store[action.id] = ActionRunStatus.RUNNING
        result = await run_action(action_trail=action_trail, **action.model_dump())

        # Mark the action as completed
        task_status_store[action.id] = ActionRunStatus.SUCCESS

        # Store the result in the action result store.
        # Every action has its own result and the trail of actions that led to it.
        # The schema is {<action ID> : <action result>, ...}
        action_result_store[action.id] = action_trail | {action.id: result}
        logger.debug(f"Action {action.id!r} completed with result {result}.")

        # Broadcast the results to the next actions and enqueue them
        for next_action_id in adj_list[action.id]:
            if next_action_id not in task_status_store:
                task_status_store[next_action_id] = ActionRunStatus.QUEUED
                ready_tasks.put_nowait(
                    ActionRun(
                        run_id=run_id,
                        action_id=next_action_id,
                    )
                )

    except TimeoutError:
        logger.error(
            f"Action {action.id} timed out waiting for dependencies {dependencies}."
        )
    except asyncio.CancelledError:
        logger.warning(f"Action {action.id!r} was cancelled.")
    except Exception as e:
        logger.error(f"Action {action.id!r} failed with error {e}.")
    finally:
        if task_status_store[action.id] != ActionRunStatus.SUCCESS:
            # Exception was raised before the action was marked as successful
            task_status_store[action.id] = ActionRunStatus.FAILURE
        running_tasks_store.pop(action.id, None)
        logger.debug(f"Remaining tasks: {running_tasks_store.keys()}")
