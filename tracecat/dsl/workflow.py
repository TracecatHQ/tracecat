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

    from tracecat.dsl.models import ActionStatement, DSLNodeResult
    from tracecat.dsl.io import resolve_success_output
    from tracecat.expressions.shared import ExprContext, IterableExpr
    from tracecat.dsl.models import ActionTest
    from tracecat.expressions.engine import TemplateExpression
    from tracecat.expressions.eval import (
        eval_templated_object,
        extract_templated_secrets,
    )
    from tracecat.types.auth import Role
    from tracecat.auth.sandbox import AuthSandbox
    from tracecat.contexts import ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.common import DSLInput
    from tracecat.logging import logger
    from tracecat.registry import registry
    from tracecat.identifiers import WorkflowID


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput
    wf_id: WorkflowID


class DSLContext(TypedDict):
    INPUTS: dict[str, Any]
    """DSL Static Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""

    TRIGGER: dict[str, Any]
    """DSL Trigger dynamic inputs context"""


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
        self.queue.put_nowait(self.dsl.entrypoint.ref)
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
        self.tracecat_wf_id = args.wf_id
        # Temporal workflow execution ID == Tracecat workflow run ID
        self.tracecat_wf_run_id = workflow.info().workflow_id

        self.run_ctx = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=workflow.info().workflow_id,
            wf_run_id=workflow.info().run_id,
        )
        self.logger = logger.bind(run_ctx=self.run_ctx, role=self.role)
        ctx_logger.set(self.logger)

        self.dsl = args.dsl
        self.context = DSLContext(
            ACTIONS={},
            INPUTS=self.dsl.inputs,
            TRIGGER=self.dsl.trigger_inputs,
        )
        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}
        self.action_test_map = {test.ref: test for test in self.dsl.tests}
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
        3. If there's an ActionTest, skip execution and return the patched result.
            - Note that we still schedule the task for execution, but we don't actually run it.
        """
        with self.logger.contextualize(task_ref=task.ref):
            if self._should_skip_execution(task):
                self._mark_task(task.ref, TaskMarker.SKIP)
                return

            # Check for an action test
            act_test = (
                self.action_test_map.get(task.ref)
                if self.dsl.config.enable_runtime_tests
                else None
            )
            self.logger.info("Executing task", act_test=act_test)
            # TODO: Set a retry policy for the activity
            activity_result = await workflow.execute_activity(
                _udf_key_to_activity_name(task.action),
                arg=UDFActionInput(
                    task=task,
                    role=self.role,
                    run_context=self.run_ctx,
                    exec_context=self.context,
                    action_test=act_test,
                ),
                start_to_close_timeout=timedelta(minutes=1),
            )
            self.context[ExprContext.ACTIONS][task.ref] = DSLNodeResult(
                result=activity_result,
                result_typename=type(activity_result).__name__,
            )

    def _should_skip_execution(self, task: ActionStatement) -> bool:
        if self.scheduler.marked_tasks.get(task.ref) == TaskMarker.SKIP:
            self.logger.info("Task marked for skipping, skipped")
            return True
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = TemplateExpression(task.run_if, operand=self.context)
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
    exec_context: dict[ExprContext, dict[str, Any]]
    run_context: RunContext
    action_test: ActionTest | None = None


def _udf_key_to_activity_name(key: str) -> str:
    return key.replace(".", "__")


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def init(cls):
        """Create activity methods from the UDF registry and attach them to DSLActivities."""
        global registry
        for key in registry.keys:
            # path.to.method_name -> path__to__method_name
            method_name = _udf_key_to_activity_name(key)

            async def async_wrapper(input: UDFActionInput):
                return await cls.run_udf(input)

            fn = activity.defn(name=method_name)(async_wrapper)
            setattr(cls, method_name, staticmethod(fn))

        return cls

    @classmethod
    def get_activities(cls) -> list[Callable[[UDFActionInput], Any]]:
        """Get all loaded UDFs in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(getattr(cls, method_name), "__temporal_activity_definition")
        ]

    @classmethod
    def load(cls) -> list[Callable[[UDFActionInput], Any]]:
        """Load and return all UDFs in the class."""
        cls.init()
        return cls.get_activities()

    async def run_udf(input: UDFActionInput) -> Any:
        ctx_run.set(input.run_context)
        ctx_role.set(input.role)
        task = input.task

        act_logger = logger.bind(
            task_ref=task.ref, wf_id=input.run_context.wf_id, role=input.role
        )

        # Multi-phase expression resolution
        # ---------------------------------
        # 1. Resolve all expressions in all shared (non action-local) contexts
        # 2. Enter loop iteration (if any)
        # 3. Resolve all action-local expressions

        # Set
        # If there's a for loop, we need to process this action in parallel

        # Evaluate `SECRETS` context
        # --------------------------
        # Securely inject secrets into the task arguments
        # 1. Find all secrets in the task arguments
        # 2. Load the secrets
        # 3. Inject the secrets into the task arguments using an enriched context
        # NOTE: Regardless of loop iteration, we should only make this call/substitution once!!
        secret_refs = extract_templated_secrets(task.args)
        async with AuthSandbox(secrets=secret_refs, target="context") as sandbox:
            # Skip evaluation of action-local expressions
            args = eval_templated_object(
                task.args,
                operand={**input.exec_context, ExprContext.SECRETS: sandbox.secrets},
                exclude={ExprContext.LOCAL_VARS},
            )
        # When we're here, we've populated the task arguments with shared context values
        type = task.action
        ctx_logger.set(act_logger)

        udf = registry[type]
        act_logger.info(
            "Run udf", task_ref=task.ref, type=type, is_async=udf.is_async, args=args
        )

        act_test = input.action_test
        if act_test and act_test.enable:
            act_logger.warning(
                f"Action test enabled, mocking the output of {task.ref!r}."
                " You should not use this in production workflows."
            )
            if act_test.validate_args:
                udf.validate_args(**args)
            result = await resolve_success_output(act_test)

        elif task.for_each:
            # If there's a loop, we need to process this action in parallel

            # Evaluate the loop expression
            iterable_exprs: IterableExpr | list[IterableExpr] = eval_templated_object(
                task.for_each, operand=input.exec_context
            )
            if isinstance(iterable_exprs, IterableExpr):
                iterable_exprs = [iterable_exprs]
            elif not (
                isinstance(iterable_exprs, list)
                and all(isinstance(expr, IterableExpr) for expr in iterable_exprs)
            ):
                raise ValueError(
                    "Invalid for_each expression. Must be an IterableExpr or a list of IterableExprs."
                )

            act_logger.info("Running in loop")
            act_logger.debug("Iterables", iter_expr=iterable_exprs)

            # Assert that all length of the iterables are the same
            # This is a requirement for parallel processing
            if len({len(expr.collection) for expr in iterable_exprs}) != 1:
                raise ValueError("All iterables must be of the same length")

            tasks: list[asyncio.Task] = []

            # Create a generator that zips the iterables together

            async with asyncio.TaskGroup() as tg:
                for i, items in enumerate(zip(*iterable_exprs, strict=False)):
                    act_logger.debug("Loop iteration", iteration=i)
                    # Patch the context with the loop item and evaluate the action-local expressions
                    # We're copying this so that we don't pollute the original context
                    # Currently, the only source of action-local expressions is the loop iteration
                    # In the future, we may have other sources of action-local expressions
                    patched_context = input.exec_context.copy()
                    act_logger.debug(
                        "Context before patch", patched_context=patched_context
                    )
                    for iterator_path, iterator_value in items:
                        patch_object(
                            patched_context, path=iterator_path, value=iterator_value
                        )
                    act_logger.debug("Patched context", patched_context=patched_context)
                    patched_args = eval_templated_object(
                        args, operand=patched_context, exclude={ExprContext.SECRETS}
                    )
                    act_logger.debug("Patched args", patched_args=patched_args)
                    task = tg.create_task(udf.run_async(patched_args))
                    tasks.append(task)

            result = [task.result() for task in tasks]

        else:
            result = await udf.run_async(args)

        act_logger.debug("Result", result=result)
        return result


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


if __name__ == "__main__":
    print(DSLActivities.load())
    registry.init()
    DSLActivities.init()
    print(DSLActivities.load())
