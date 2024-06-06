from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self, TypedDict

import yaml
from temporalio import activity, workflow

from tracecat.contexts import RunContext

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    from tracecat.logging import logger
    from pydantic import BaseModel, ConfigDict, Field, model_validator

    from tracecat.auth import Role
    from tracecat.contexts import ctx_role, ctx_run, ctx_logger
    from tracecat.registry import registry
    from tracecat import templates


SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class DSLError(ValueError):
    pass


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
    inputs: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_yaml(path: str | Path) -> DSLInput:
        with Path(path).open("r") as f:
            yaml_str = f.read()
        dsl_dict = yaml.safe_load(yaml_str)
        return DSLInput.model_validate(dsl_dict)

    def to_yaml(self, path: str | Path) -> None:
        with Path(path).expanduser().resolve().open("w") as f:
            yaml.dump(self.model_dump(), f)

    def dump_yaml(self) -> str:
        return yaml.dump(self.model_dump())

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.actions:
            raise DSLError("At least one action must be defined")
        if len({action.ref for action in self.actions}) != len(self.actions):
            raise DSLError("All task.ref must be unique")
        valid_actions = tuple(action.ref for action in self.actions)
        if self.entrypoint not in valid_actions:
            raise DSLError(f"Entrypoint must be one of the actions {valid_actions!r}")
        n_entrypoints = sum(1 for action in self.actions if not action.depends_on)
        if n_entrypoints != 1:
            raise DSLError(f"Expected 1 entrypoint, got {n_entrypoints}")
        return self


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput


class ActionStatement(BaseModel):
    ref: str = Field(pattern=SLUG_PATTERN)
    """Unique reference for the task"""

    action: str = Field(pattern=ACTION_TYPE_PATTERN)
    """Namespaced action type"""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments for the action"""

    depends_on: list[str] = Field(default_factory=list)
    """Task dependencies"""

    run_if: Annotated[str | None, Field(default=None), templates.TemplateValidator()]


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

    _queue_wait_timeout = 1

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
        # self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()

        self.executor = activity_coro
        self.logger = ctx_logger.get(logger)

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
        self.logger.info("Task completed", task_ref=task_ref)

        # Update the indegrees of the tasks
        async with asyncio.TaskGroup() as tg:
            for next_task_ref in self.adj[task_ref]:
                self.indegrees[next_task_ref] -= 1
                if self.indegrees[next_task_ref] == 0:
                    tg.create_task(self.queue.put(next_task_ref))

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
        self.logger.info("All tasks completed")

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
    async def run(self, args: DSLRunArgs) -> DSLContext:
        # Setup
        self.role = args.role
        self.run_ctx = RunContext(
            wf_id=workflow.info().workflow_id,
            wf_run_id=workflow.info().run_id,
        )
        self.logger = logger.bind(wf_id=self.run_ctx.wf_id, role=self.role)
        ctx_logger.set(self.logger)

        self.dsl = args.dsl
        self.context = DSLContext(INPUTS=self.dsl.inputs, ACTIONS={})
        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}
        self.logger.info("Running DSL task workflow")

        self.scheduler = DSLScheduler(activity_coro=self.execute_task, dsl=self.dsl)
        await self.scheduler.start()

        self.logger.info("DSL workflow completed")
        return self.context

    async def execute_task(self, task: ActionStatement) -> None:
        """Purely execute a task and manage the results.

        Preflight checks
        ---------------
        1. Evaluate `run_if` condition
        2. Resolve all templated arguments
        """
        # Evaluate the `run_if` condition
        with self.logger.contextualize(task_ref=task.ref):
            if task.run_if is not None:
                expr = templates.TemplateExpression(task.run_if, operand=self.context)
                self.logger.info("`run_if` condition", task_run_if=task.run_if)
                if not bool(expr.result()):
                    self.logger.info("Task skipped")
                    return

            self.logger.info("Executing task")
            # TODO: Securely inject secrets into the task arguments
            # 1. Find all secrets in the task arguments
            # 2. Load the secrets
            # 3. Inject the secrets into the task arguments using an enriched context

            # Resolve all templated arguments
            processed_args = templates.eval_templated_object(
                task.args, operand=self.context
            )

            # TODO: Set a retry policy for the activity
            activity_result = await workflow.execute_activity(
                "run_udf",
                arg=UDFActionArgs(
                    type=task.action,
                    args=processed_args,
                    role=self.role,
                    context=self.run_ctx,
                ),
                start_to_close_timeout=timedelta(minutes=1),
            )
            self.context["ACTIONS"][task.ref] = DSLNodeResult(
                result=activity_result,
                result_typename=type(activity_result).__name__,
            )


class UDFActionArgs(BaseModel):
    type: str
    args: dict[str, Any]
    role: Role
    context: RunContext


class DSLActivities:
    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @staticmethod
    @activity.defn
    async def run_udf(action: UDFActionArgs) -> Any:
        ctx_run.set(action.context)
        ctx_role.set(action.role)
        act_logger = logger.bind(wf_id=action.context.wf_id, role=action.role)
        ctx_logger.set(act_logger)

        udf = registry[action.type]
        act_logger.info(
            "Run udf",
            type=action.type,
            is_async=udf.is_async,
            args=action.args,
        )
        if udf.is_async:
            result = await udf.fn(**action.args)
        else:
            result = await asyncio.to_thread(udf.fn, **action.args)
        act_logger.info("Result", result=result)
        return result


# Dynamically register all static methods as activities
dsl_activities = [
    getattr(DSLActivities, method_name)
    for method_name in dir(DSLActivities)
    if hasattr(getattr(DSLActivities, method_name), "__temporal_activity_definition")
]
