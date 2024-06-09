from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import timedelta
from enum import StrEnum, auto
from typing import Any, TypedDict

from temporalio import activity, workflow

from tracecat.contexts import RunContext

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    from pydantic import BaseModel

    from tracecat.auth.credentials import Role
    from tracecat.auth.sandbox import AuthSandbox
    from tracecat import templates
    from tracecat.contexts import ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.common import ActionStatement, DSLInput
    from tracecat.logging import logger
    from tracecat.registry import registry
    from tracecat.db.schemas import Secret  # noqa


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput


class DSLContext(TypedDict):
    INPUTS: dict[str, Any]
    """DSL Static Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""

    TRIGGER: dict[str, Any]
    """DSL Trigger dynamic inputs context"""


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str


class TaskMarker(StrEnum):
    SKIP = auto()
    TERMINATED = auto()


class SkipStrategy(StrEnum):
    ISOLATE = auto()
    PROPAGATE = auto()


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 1
    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        activity_coro: Callable[[ActionStatement], Coroutine[Any, Any, None]],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
    ):
        self.dsl = dsl
        self.tasks: dict[str, ActionStatement] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.indegrees: dict[str, int] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        # self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()
        # Tasks can be marked for termination.
        # This is useful for tasks that are
        self.marked_tasks: dict[str, TaskMarker] = {}
        self.skip_strategy = skip_strategy
        self._task_exceptions = {}

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

        # For now, tasks that were marked to skip also join this set
        self.completed_tasks.add(task_ref)
        self.logger.info("Task completed", task_ref=task_ref)

        # Update child indegrees
        # ----------------------
        # Treat a skipped task as completed, update as usual.
        # Any child task whose indegree reaches 0 must check if all its parent
        # dependencies we skipped. if ALL parents were skipped, then the child
        # task is also marked for skipping. If ANY parent was not skipped, then
        # the child task is added to the queue.

        # The intuition here is that if you have a task that becomes unreachable,
        # then some of its children will also become unreachable. A node becomes unreachable
        # if there is no one successful oath that leads to it.

        # This allows us to have diamond-shaped graphs where some branches can be skipped
        # but at the join point, if any parent was not skipped, then the child can still be executed.
        async with asyncio.TaskGroup() as tg:
            for next_task_ref in self.adj[task_ref]:
                self.indegrees[next_task_ref] -= 1
                if self.indegrees[next_task_ref] == 0:
                    if (
                        self.skip_strategy == SkipStrategy.PROPAGATE
                        and self.task_is_reachable(next_task_ref)
                    ):
                        self.mark_task(next_task_ref, TaskMarker.SKIP)
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

    def mark_task(self, task_ref: str, marker: TaskMarker) -> None:
        self.marked_tasks[task_ref] = marker

    def task_is_reachable(self, task_ref: str) -> bool:
        """Check whether a task is reachable by checking if all its dependencies were skipped."""
        return all(
            self.marked_tasks.get(parent) == TaskMarker.SKIP
            for parent in self.tasks[task_ref].depends_on
        )

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
        self.context = DSLContext(
            ACTIONS={},
            INPUTS=self.dsl.inputs,
            TRIGGER=self.dsl.trigger_inputs,
        )
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
        with self.logger.contextualize(task_ref=task.ref):
            if self._should_skip_execution(task):
                self._mark_task(task.ref, TaskMarker.SKIP)
                return

            self.logger.info("Executing task")
            # TODO: Set a retry policy for the activity
            activity_result = await workflow.execute_activity(
                "run_udf",
                arg=UDFActionInput(
                    task=task,
                    role=self.role,
                    run_context=self.run_ctx,
                    exec_context=self.context,
                ),
                start_to_close_timeout=timedelta(minutes=1),
            )
            self.context["ACTIONS"][task.ref] = DSLNodeResult(
                result=activity_result,
                result_typename=type(activity_result).__name__,
            )

    def _should_skip_execution(self, task: ActionStatement) -> bool:
        if self.scheduler.marked_tasks.get(task.ref) == TaskMarker.SKIP:
            self.logger.info("Task marked for skipping, skipped")
            return True
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = templates.TemplateExpression(task.run_if, operand=self.context)
            self.logger.debug("`run_if` condition", task_run_if=task.run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition did not pass, skipped")
                return True
        return False

    def _mark_task(self, task_ref: str, marker: TaskMarker) -> None:
        self.scheduler.mark_task(task_ref, marker)


class UDFActionInput(BaseModel):
    task: ActionStatement
    role: Role
    exec_context: dict[str, Any]
    run_context: RunContext


class DSLActivities:
    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @staticmethod
    @activity.defn
    async def run_udf(input: UDFActionInput) -> Any:
        ctx_run.set(input.run_context)
        ctx_role.set(input.role)
        task = input.task

        act_logger = logger.bind(
            task_ref=task.ref, wf_id=input.run_context.wf_id, role=input.role
        )

        # Securely inject secrets into the task arguments
        # 1. Find all secrets in the task arguments
        # 2. Load the secrets
        # 3. Inject the secrets into the task arguments using an enriched context
        secret_refs = templates.extract_templated_secrets(task.args)
        logger.warning("Secrets", secret_refs=secret_refs)
        async with AuthSandbox(secrets=secret_refs, target="context") as sandbox:
            # Resolve all templated arguments
            logger.info("Evaluating task arguments", secrets=sandbox.secrets)
            args = templates.eval_templated_object(
                task.args, operand={**input.exec_context, "SECRETS": sandbox.secrets}
            )
        type = task.action
        ctx_logger.set(act_logger)

        udf = registry[type]
        act_logger.info(
            "Run udf", task_ref=task.ref, type=type, is_async=udf.is_async, args=args
        )
        if udf.is_async:
            result = await udf.fn(**args)
        else:
            result = await asyncio.to_thread(udf.fn, **args)
        act_logger.info("Result", result=result)
        return result


# Dynamically register all static methods as activities
dsl_activities = [
    getattr(DSLActivities, method_name)
    for method_name in dir(DSLActivities)
    if hasattr(getattr(DSLActivities, method_name), "__temporal_activity_definition")
]
