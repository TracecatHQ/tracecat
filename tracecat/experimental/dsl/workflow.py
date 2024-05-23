from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from pydantic import validator
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    from pydantic import BaseModel, ConfigDict, Field

    from loguru import logger
    from tracecat.experimental.registry import registry
    from tracecat.experimental.templates.eval import eval_templated_object


SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class Trigger(BaseModel):
    type: Literal["schedule", "webhook"]
    ref: str = Field(pattern=SLUG_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)


class DSLConfig(BaseModel):
    scheduler: Literal["static", "dynamic"] = "dynamic"


class DSLInput(BaseModel):
    """DSL definition for a workflow.

    The difference between this and a normal workflow engine is that here,
    our workflow execution order is defined by the DSL itself, independent
    of a workflow scheduler.

    With a traditional
    This allows the execution of the workflow to be fully deterministic.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    title: str
    description: str
    entrypoint: str
    actions: list[ActionStatement]
    config: DSLConfig = Field(default_factory=DSLConfig)
    triggers: list[Trigger] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_yaml(path: str | Path) -> DSLInput:
        with Path(path).open("r") as f:
            yaml_str = f.read()
        dsl_dict = yaml.safe_load(yaml_str)
        return DSLInput.model_validate(dsl_dict)

    def to_yaml(self, path: str | Path) -> None:
        with Path(path).open("w") as f:
            yaml.dump(self.model_dump(), f)

    @validator("actions", pre=True)
    def validate_actions(cls, v: list[dict[str, Any]]) -> list[ActionStatement]:
        if not v:
            return []
        # Ensure all task.ref are unique
        if len({action["ref"] for action in v}) != len(v):
            raise ValueError("All task.ref must be unique")
        return [ActionStatement.model_validate(task) for task in v]


class ActionStatement(BaseModel):
    ref: str = Field(pattern=SLUG_PATTERN)
    """Unique reference for the task"""

    action: str = Field(pattern=ACTION_TYPE_PATTERN)
    """Namespaced action type"""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments for the action"""

    depends_on: list[str] = Field(default_factory=list)
    """Task dependencies"""


class DSLContext(TypedDict):
    INPUTS: dict[str, Any]
    """DSL Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 2

    def __init__(
        self,
        *,
        activity_coro: Callable[[ActionStatement], Coroutine[Any, Any, None]],
        dsl: DSLInput,
    ):
        self.dsl = dsl
        self.tasks: dict[str, ActionStatement] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.indegrees: dict[str, int] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()

        self.executor = activity_coro

        for task in dsl.actions:
            self.tasks[task.ref] = task
            self.indegrees[task.ref] = len(task.depends_on)
            for dep in task.depends_on:
                self.adj[dep].add(task.ref)

    async def _dynamic_task(self, task_ref: str) -> None:
        """Dynamic task execution.

        Tasks
        -----
        1. Run the task
        2. Manage the indegrees of the tasks
        """
        task = self.tasks[task_ref]
        await self.executor(task)

        self.completed_tasks.add(task_ref)
        logger.info("Task completed", task_ref=task_ref)

        # Update the indegrees of the tasks
        for next_task_ref in self.adj[task_ref]:
            self.indegrees[next_task_ref] -= 1
            if self.indegrees[next_task_ref] == 0:
                self.queue.put_nowait(next_task_ref)

    async def dynamic_start(self) -> None:
        """Run the scheduler in dynamic mode."""
        self.queue.put_nowait(self.dsl.entrypoint)
        while not self.queue.empty() or len(self.completed_tasks) < len(self.tasks):
            try:
                task_ref = await asyncio.wait_for(
                    self.queue.get(), timeout=self._queue_wait_timeout
                )
            except TimeoutError:
                continue

            asyncio.create_task(self._dynamic_task(task_ref))
        logger.info("All tasks completed")

    async def _static_task(self, task_ref: str) -> None:
        raise NotImplementedError

    async def static_start(self) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        if self.dsl.config.scheduler == "dynamic":
            return await self.dynamic_start()
        else:
            return await self.static_start()


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow."""

    @workflow.run
    async def run(self, dsl: DSLInput) -> DSLContext:
        # Setup
        self.dsl = dsl
        self.context = DSLContext(INPUTS=dsl.variables, ACTIONS={})
        self.dep_list = {task.ref: task.depends_on for task in dsl.actions}
        wfr_id = workflow.info().run_id
        workflow.logger.info(f"Running DSL task workflow {wfr_id}")

        self.scheduler = DSLScheduler(activity_coro=self.execute_task, dsl=dsl)
        await self.scheduler.start()

        workflow.logger.info("DSL workflow completed")
        return self.context

    async def execute_task(self, task: ActionStatement) -> None:
        """Purely execute a task and manage the results."""
        # Resolve all templated arguments
        workflow.logger.info(f"Executing task {task.ref}")
        processed_args = eval_templated_object(task.args, operand=self.context)

        # TODO: Set a retry policy for the activity
        activity_result = await workflow.execute_activity(
            "run_udf",
            arg=UDFAction(type=task.action, args=processed_args),
            start_to_close_timeout=timedelta(minutes=1),
        )
        self.context["ACTIONS"][task.ref] = DSLNodeResult(
            result=activity_result,
            result_typename=type(activity_result).__name__,
        )


class UDFAction(BaseModel):
    type: str
    args: dict[str, Any]


class DSLActivities:
    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @staticmethod
    @activity.defn
    async def run_udf(input: UDFAction) -> Any:
        activity.logger.info("Run udf")
        activity.logger.info(f"{input = }")
        udf = registry[input.type]
        if udf.is_async:
            result = await udf.fn(**input.args)
        else:
            # Force it to be async
            result = await asyncio.to_thread(udf.fn, **input.args)
        activity.logger.info(f"Result: {result}")
        return result


# Dynamically register all static methods as activities
dsl_activities = [
    getattr(DSLActivities, method_name)
    for method_name in dir(DSLActivities)
    if hasattr(getattr(DSLActivities, method_name), "__temporal_activity_definition")
]
