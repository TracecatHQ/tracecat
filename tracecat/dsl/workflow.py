from __future__ import annotations

import asyncio
import itertools
import json
from collections import defaultdict
from collections.abc import (
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
)
from typing import Any, Generic, TypedDict, cast

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

with workflow.unsafe.imports_passed_through():
    import httpx
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    import lark  # noqa
    from pydantic import BaseModel, ValidationError

    from tracecat import config, identifiers
    from tracecat.auth.sandbox import AuthSandbox
    from tracecat.concurrency import GatheringTaskGroup
    from tracecat.contexts import RunContext, ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.common import DSLInput, DSLRunArgs, ExecuteChildWorkflowArgs
    from tracecat.dsl.enums import FailStrategy, LoopStrategy, SkipStrategy, TaskMarker
    from tracecat.dsl.io import resolve_success_output
    from tracecat.dsl.models import (
        ActionStatement,
        ActionTest,
        ArgsT,
        DSLConfig,
        DSLNodeResult,
    )
    from tracecat.dsl.validation import (
        ValidateTriggerInputsActivityInputs,
        validate_trigger_inputs_activity,
    )
    from tracecat.expressions.core import TemplateExpression
    from tracecat.expressions.eval import (
        eval_templated_object,
        extract_templated_secrets,
        get_iterables_from_expression,
    )
    from tracecat.expressions.shared import ExprContext, context_locator
    from tracecat.logger import logger
    from tracecat.parse import traverse_leaves
    from tracecat.registry.manager import RegistryManager
    from tracecat.secrets.common import apply_masks_object
    from tracecat.types.auth import Role
    from tracecat.types.exceptions import (
        TracecatCredentialsError,
        TracecatDSLError,
        TracecatException,
        TracecatExpressionError,
        TracecatNotFoundError,
        TracecatValidationError,
    )
    from tracecat.types.validation import ValidationResult
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
    )
    from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs
    from tracecat.workflow.schedules.models import GetScheduleActivityInputs
    from tracecat.workflow.schedules.service import WorkflowSchedulesService


class DSLContext(TypedDict, total=False):
    INPUTS: dict[str, Any]
    """DSL Static Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""

    TRIGGER: dict[str, Any]
    """DSL Trigger dynamic inputs context"""

    ENV: DSLEnvironment
    """DSL Environment context. Has metadata about the workflow."""

    @staticmethod
    def create_default(
        INPUTS: dict[str, Any] | None = None,
        ACTIONS: dict[str, Any] | None = None,
        TRIGGER: dict[str, Any] | None = None,
        ENV: dict[str, Any] | None = None,
    ) -> DSLContext:
        return DSLContext(
            INPUTS=INPUTS or {},
            ACTIONS=ACTIONS or {},
            TRIGGER=TRIGGER or {},
            ENV=ENV or {},
        )


class DSLEnvironment(TypedDict, total=False):
    """DSL Environment context. Has metadata about the workflow."""

    workflow: dict[str, Any]
    """Metadata about the workflow."""

    environment: str
    """Target environment for the workflow."""

    variables: dict[str, Any]
    """Environment variables."""

    registry_version: str
    """The registry version to use for the workflow."""


class DSLExecutionError(TypedDict, total=False):
    """A proxy for an exception.

    This is the object that gets returned in place of an exception returned when
    using `asyncio.gather(..., return_exceptions=True)`, as Exception types aren't serializable."""

    is_error: bool
    """A flag to indicate that this object is an error."""

    type: str
    """The type of the exception. e.g. `ValueError`"""

    message: str
    """The message of the exception."""

    @staticmethod
    def from_exception(e: BaseException) -> DSLExecutionError:
        return DSLExecutionError(
            is_error=True,
            type=e.__class__.__name__,
            message=str(e),
        )


non_retryable_error_types = [
    # General
    Exception.__name__,
    TypeError.__name__,
    ValueError.__name__,
    RuntimeError.__name__,
    # Pydantic
    ValidationError.__name__,
    # Tracecat
    TracecatException.__name__,
    TracecatExpressionError.__name__,
    TracecatValidationError.__name__,
    TracecatDSLError.__name__,
    TracecatCredentialsError.__name__,
    # Temporal
    ApplicationError.__name__,
    ChildWorkflowError.__name__,
    FailureError.__name__,
]


retry_policies = {
    "activity:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=non_retryable_error_types,
    ),
    "workflow:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=non_retryable_error_types,
    ),
}


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 1
    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        activity_coro: Callable[[ActionStatement[ArgsT]], Coroutine[Any, Any, None]],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
    ):
        self.dsl = dsl
        self.tasks: dict[str, ActionStatement[ArgsT]] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.indegrees: dict[str, int] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        # self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()
        # Tasks can be marked for termination.
        # This is useful for tasks that are
        self.marked_tasks: dict[str, TaskMarker] = {}
        self.skip_strategy = skip_strategy
        self.task_exceptions: dict[str, BaseException] = {}

        self.executor = activity_coro
        self.logger = ctx_logger.get(logger).bind(unit="dsl-scheduler")

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
        try:
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
        except ActivityError as e:
            self.logger.error(
                "Activity error in DSLScheduler",
                task_ref=task_ref,
                msg=e.message,
                retry_state=e.retry_state,
            )
            self.task_exceptions[task_ref] = e
        except ApplicationError as e:
            self.logger.error(
                "Application error in DSLScheduler",
                task_ref=task_ref,
                msg=e.message,
                non_retryable=e.non_retryable,
            )
            self.task_exceptions[task_ref] = e
        except Exception as e:
            self.logger.error(
                "Unexpected error in DSLScheduler", task_ref=task_ref, error=e
            )
            self.task_exceptions[task_ref] = e

    async def dynamic_start(self) -> None:
        """Run the scheduler in dynamic mode."""
        self.queue.put_nowait(self.dsl.entrypoint.ref)
        while not self.task_exceptions and (
            not self.queue.empty() or len(self.completed_tasks) < len(self.tasks)
        ):
            self.logger.trace(
                "Waiting for tasks.",
                qsize=self.queue.qsize(),
                n_completed=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            try:
                task_ref = await asyncio.wait_for(
                    self.queue.get(), timeout=self._queue_wait_timeout
                )
            except TimeoutError:
                continue

            asyncio.create_task(self._dynamic_task(task_ref))
        if self.task_exceptions:
            self.logger.error(
                "DSLScheduler got task exceptions, stopping...",
                n_exceptions=len(self.task_exceptions),
                exceptions=self.task_exceptions,
                n_completed=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            raise ApplicationError(
                "Task exceptions occurred", *self.task_exceptions.values()
            )
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

    def start(self) -> Awaitable[None]:
        if self.dsl.config.scheduler == "dynamic":
            return self.dynamic_start()
        else:
            return self.static_start()


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow."""

    @workflow.run
    async def run(self, args: DSLRunArgs) -> Any:
        # Set runtime args
        self.role = args.role
        self.start_to_close_timeout = args.timeout
        ctx_role.set(self.role)
        wf_info = workflow.info()

        self.run_ctx = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=wf_info.run_id,
        )
        ctx_run.set(self.run_ctx)

        self.logger = logger.bind(
            run_ctx=self.run_ctx, role=self.role, unit="dsl-workflow-runner"
        )
        ctx_logger.set(self.logger)
        self.logger.debug("DSL workflow started", args=args)

        # Setup DSL context
        if args.dsl:
            # Use the provided DSL
            self.logger.debug("Using provided workflow definition")
            self.dsl = args.dsl
            self.dispatch_type = "push"
        else:
            # Otherwise, fetch the latest workflow definition
            self.logger.debug("Fetching latest workflow definition")
            try:
                self.dsl = await self._get_workflow_definition(args.wf_id)
            except TracecatException as e:
                self.logger.error("Failed to fetch workflow definition")
                raise ApplicationError(
                    "Failed to fetch workflow definition",
                    non_retryable=True,
                    type=e.__class__.__name__,
                ) from e
            self.dispatch_type = "pull"

        if "runtime_config" in args.model_fields_set:
            # Use the override runtime config if it's set
            self.runtime_config = args.runtime_config
        else:
            # Otherwise default to the DSL config
            self.runtime_config = self.dsl.config

        # Set trigger inputs
        if args.schedule_id:
            self.logger.debug("Fetching schedule trigger inputs")
            try:
                trigger_inputs = await self._get_schedule_trigger_inputs(
                    schedule_id=args.schedule_id, worflow_id=args.wf_id
                )
            except TracecatNotFoundError as e:
                raise ApplicationError(
                    "Failed to fetch trigger inputs as the schedule was not found",
                    non_retryable=True,
                    type=e.__class__.__name__,
                ) from e
        else:
            self.logger.debug("Using provided trigger inputs")
            trigger_inputs = args.trigger_inputs or {}

        try:
            validation_result = await self._validate_trigger_inputs(trigger_inputs)
            logger.info("Trigger inputs are valid", validation_result=validation_result)
        except ValidationError as e:
            logger.error("Failed to validate trigger inputs", error=e.errors())
            raise ApplicationError(
                (
                    "Failed to validate trigger inputs"
                    f"\n\n{json.dumps(e.errors(), indent=2)}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

        self.context = DSLContext(
            ACTIONS={},
            INPUTS=self.dsl.inputs,
            TRIGGER=trigger_inputs,
            ENV=DSLEnvironment(
                workflow={
                    "start_time": wf_info.start_time,
                    "dispatch_type": self.dispatch_type,
                },
                environment=self.runtime_config.environment,
                variables={},
                registry_version=self.runtime_config.registry_version,
            ),
        )

        self.registry_version = self.runtime_config.registry_version

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}
        self.action_test_map = {test.ref: test for test in self.dsl.tests}

        self.logger.info(
            "Running DSL task workflow",
            runtime_config=self.runtime_config,
            timeout=self.start_to_close_timeout,
        )

        self.scheduler = DSLScheduler(activity_coro=self.execute_task, dsl=self.dsl)
        try:
            await self.scheduler.start()
        except ApplicationError as e:
            raise ApplicationError(
                e.message, non_retryable=True, type=e.__class__.__name__
            ) from e
        except Exception as e:
            msg = f"DSL workflow execution failed with unexpected error: {e}"
            raise ApplicationError(
                msg,
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

        try:
            self.logger.info("DSL workflow completed")
            return self._handle_return()
        except TracecatExpressionError as e:
            raise ApplicationError(
                f"Couldn't parse return value expression: {e}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except Exception as e:
            raise ApplicationError(
                f"Unexpected error handling return value: {e}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

    async def execute_task(self, task: ActionStatement[ArgsT]) -> None:
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

            try:
                logger.info("Begin task execution", task_ref=task.ref)
                # Check for a child workflow
                if self._should_execute_child_workflow(task):
                    # NOTE: We don't support (nor recommend, unless a use case is justified) passing SECRETS to child workflows
                    # 1. Prepare the child workflow
                    logger.trace("Preparing child workflow")
                    child_run_args = await self._prepare_child_workflow(task)
                    logger.trace(
                        "Child workflow prepared", child_run_args=child_run_args
                    )
                    # This is the original child runtime args, preset by the DSL
                    # In contrast, task.args are the runtime args that the parent workflow provided
                    action_result = await self._execute_child_workflow(
                        task=task, child_run_args=child_run_args
                    )

                else:
                    # Below this point, we're executing the task
                    # Check for an action test
                    act_test = (
                        self.action_test_map.get(task.ref)
                        if self.runtime_config.enable_runtime_tests
                        else None
                    )
                    logger.trace(
                        "Running action",
                        act_test=act_test,
                        task_ref=task.ref,
                        act_test_map=self.action_test_map,
                        runtime_config=self.runtime_config,
                    )
                    action_result = await self._run_action(task, action_test=act_test)

                self.context[ExprContext.ACTIONS][task.ref] = DSLNodeResult(
                    result=action_result,
                    result_typename=type(action_result).__name__,
                )
            except ActivityError as e:
                logger.error("Activity execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except ChildWorkflowError as e:
                logger.error("Child workflow execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except FailureError as e:
                logger.error("Workflow execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except ValidationError as e:
                logger.error("Runtime validation error", error=e.errors())
                raise e
            except Exception as e:
                msg = f"Task execution failed with unexpected error: {e}"
                logger.error(
                    "Activity execution failed with unexpected error", error=msg
                )
                raise ApplicationError(
                    msg, non_retryable=True, type=e.__class__.__name__
                ) from e

    async def _execute_child_workflow(
        self,
        task: ActionStatement[ExecuteChildWorkflowArgs],
        child_run_args: DSLRunArgs,
    ) -> Any:
        self.logger.debug("Execute child workflow", child_run_args=child_run_args)
        if task.for_each:
            # In for loop, child run args are shared among all iterations
            return await self._execute_child_workflow_loop(
                task=task, child_run_args=child_run_args
            )
        else:
            # At this point,
            # Child run args
            # Task args here refers to the args passed to the child
            args = _evaluate_templated_args(task, context=self.context)
            self.logger.trace(
                "Executing child workflow",
                child_run_args=child_run_args,
                task_args=task.args,
                evaluated_args=args,
            )

            # Always set the trigger inputs in the child run args
            child_run_args.trigger_inputs = args.get("trigger_inputs")

            # Override the runtime config in the child run args
            # Override the environment in the child run args
            # XXX: We must use the default environment in the child workflow DSL if none is provided
            self.logger.debug(
                "Options",
                child_environment=child_run_args.runtime_config.environment,
                task_environment=args.get("environment"),
                dsl_environment=child_run_args.dsl.config.environment,
            )
            child_run_args.runtime_config.environment = (
                args.get("environment") or child_run_args.dsl.config.environment
            )
            return await self._run_child_workflow(child_run_args)

    async def _execute_child_workflow_loop(
        self,
        *,
        task: ActionStatement[ExecuteChildWorkflowArgs],
        child_run_args: DSLRunArgs,
    ) -> list[Any]:
        loop_strategy = LoopStrategy(
            task.args.get("loop_strategy", LoopStrategy.PARALLEL)
        )
        fail_strategy = FailStrategy(
            task.args.get("fail_strategy", FailStrategy.ISOLATED)
        )
        self.logger.trace(
            "Executing child workflow in loop",
            dsl_run_args=child_run_args,
            loop_strategy=loop_strategy,
            fail_strategy=fail_strategy,
        )

        iterator = iter_for_each(task=task, context=self.context)
        if loop_strategy == LoopStrategy.PARALLEL:
            action_result = await self._execute_child_workflow_batch(
                batch=iterator,
                base_run_args=child_run_args,
                fail_strategy=fail_strategy,
            )

        elif loop_strategy == LoopStrategy.BATCH:
            action_result = []
            batch_size = task.args.get("batch_size") or 16
            for batch in itertools.batched(iterator, batch_size):
                results = await self._execute_child_workflow_batch(
                    batch=batch,
                    base_run_args=child_run_args,
                    fail_strategy=fail_strategy,
                )
                action_result.extend(results)
        else:
            # Sequential
            action_result = []
            for patched_args in iterator:
                child_run_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                result = await self._run_child_workflow(child_run_args)
                action_result.append(result)
        return action_result

    async def _execute_child_workflow_batch(
        self,
        batch: Iterable[ExecuteChildWorkflowArgs],
        base_run_args: DSLRunArgs,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[Any]:
        def iter_patched_args() -> Generator[DSLRunArgs]:
            for patched_args in batch:
                cloned_args = base_run_args.model_copy()
                cloned_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                cloned_args.runtime_config = base_run_args.runtime_config.model_copy()
                cloned_args.runtime_config.environment = (
                    patched_args.get("environment")
                    or base_run_args.runtime_config.environment
                )

                yield cloned_args

        if fail_strategy == FailStrategy.ALL:
            async with GatheringTaskGroup() as tg:
                for patched_run_args in iter_patched_args():
                    logger.trace(
                        "Run child workflow batch",
                        fail_strategy=fail_strategy,
                        patched_run_args=patched_run_args,
                    )
                    tg.create_task(self._run_child_workflow(patched_run_args))
            return tg.results()
        else:
            # Isolated
            coros = []
            for patched_run_args in iter_patched_args():
                logger.trace(
                    "Run child workflow batch",
                    fail_strategy=fail_strategy,
                    patched_run_args=patched_run_args,
                )
                coro = self._run_child_workflow(patched_run_args)
                coros.append(coro)
            gather_result = await asyncio.gather(*coros, return_exceptions=True)
            result: list[DSLExecutionError | Any] = [
                DSLExecutionError.from_exception(val)
                if isinstance(val, BaseException)
                else val
                for val in gather_result
            ]
            return result

    def _handle_return(self) -> Any:
        if self.dsl.returns is None:
            # Return the context
            # XXX: Don't return ENV context for now
            self.logger.trace("Returning DSL context")
            self.context.pop(ExprContext.ENV.value, None)
            return self.context
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        return eval_templated_object(self.dsl.returns, operand=self.context)

    async def _get_workflow_definition(
        self, workflow_id: identifiers.WorkflowID, version: int | None = None
    ) -> DSLInput:
        activity_inputs = GetWorkflowDefinitionActivityInputs(
            role=self.role, workflow_id=workflow_id, version=version
        )

        self.logger.debug(
            "Running get workflow definition activity", activity_inputs=activity_inputs
        )
        return await workflow.execute_activity(
            get_workflow_definition_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    async def _validate_trigger_inputs(
        self, trigger_inputs: dict[str, Any]
    ) -> ValidationResult:
        """Validate trigger inputs.

        Note
        ----
        Not sure why we can't just run the function directly here.
        Pydantic throws an invalid JsonSchema error when we do so.
        """

        validation_result = await workflow.execute_activity(
            validate_trigger_inputs_activity,
            arg=ValidateTriggerInputsActivityInputs(
                dsl=self.dsl,
                trigger_inputs=trigger_inputs,
            ),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        return validation_result

    async def _get_schedule_trigger_inputs(
        self, schedule_id: identifiers.ScheduleID, worflow_id: identifiers.WorkflowID
    ) -> dict[str, Any]:
        """Get the trigger inputs for a schedule.

        Raises
        ------
        TracecatNotFoundError
            If the schedule is not found.
        """
        activity_inputs = GetScheduleActivityInputs(
            role=self.role, schedule_id=schedule_id, workflow_id=worflow_id
        )

        self.logger.debug(
            "Running get schedule activity", activity_inputs=activity_inputs
        )
        schedule_read = await workflow.execute_activity(
            WorkflowSchedulesService.get_schedule_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        return schedule_read.inputs

    async def _prepare_child_workflow(
        self, task: ActionStatement[ExecuteChildWorkflowArgs]
    ) -> DSLRunArgs:
        """Grab a workflow definition and create child workflow run args"""

        validated_args = _validate_action_args(task, self.registry_version)
        # environment is None here. This is coming from the action
        self.logger.trace(
            "Validated child workflow args", validated_args=validated_args
        )

        child_wf_id = validated_args["workflow_id"]
        dsl = await self._get_workflow_definition(
            workflow_id=child_wf_id, version=validated_args["version"]
        )

        self.logger.debug(
            "Got workflow definition",
            dsl=dsl,
            validated_args=validated_args,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )
        runtime_config = DSLConfig(
            # Child inherits the parent's test override config
            enable_runtime_tests=self.runtime_config.enable_runtime_tests,
            # Override the environment in the runtime config,
            # otherwise use the default provided in the workflow definition
            environment=validated_args.get("environment") or dsl.config.environment,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=child_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=validated_args["trigger_inputs"],
            runtime_config=runtime_config,
        )

    def _run_action(
        self, task: ActionStatement[ArgsT], action_test: ActionTest | None = None
    ) -> Awaitable[Any]:
        arg = UDFActionInput(
            task=task,
            role=self.role,
            run_context=self.run_ctx,
            exec_context=self.context,
            action_test=action_test,
            registry_version=self.registry_version,
        )
        self.logger.debug("RUN UDF ACTIVITY", arg=arg)
        return workflow.execute_activity(
            DSLActivities.run_action,
            arg=arg,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    def _run_child_workflow(self, run_args: DSLRunArgs) -> Awaitable[Any]:
        self.logger.info("Running child workflow", run_args=run_args)
        wf_exec_id = identifiers.workflow.exec_id(run_args.wf_id)
        return workflow.execute_child_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=workflow.info().task_queue,
            retry_policy=retry_policies["workflow:fail_fast"],
            execution_timeout=self.start_to_close_timeout,
        )

    def _should_skip_execution(self, task: ActionStatement[ArgsT]) -> bool:
        if self.scheduler.marked_tasks.get(task.ref) == TaskMarker.SKIP:
            self.logger.info("Task marked for skipping, skipped")
            return True
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = TemplateExpression(task.run_if, operand=self.context)
            self.logger.debug("`run_if` condition", task_run_if=task.run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition was not met, skipped")
                return True
        return False

    def _mark_task(self, task_ref: str, marker: TaskMarker) -> None:
        self.scheduler.mark_task(task_ref, marker)

    def _should_execute_child_workflow(self, task: ActionStatement[ArgsT]) -> bool:
        return task.action == "core.workflow.execute"


class UDFActionInput(BaseModel, Generic[ArgsT]):
    task: ActionStatement[ArgsT]
    role: Role
    exec_context: DSLContext
    run_context: RunContext
    action_test: ActionTest | None = None
    registry_version: str


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[UDFActionInput[ArgsT]], Any]]:
        """Load and return all UDFs in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(
                getattr(cls, method_name),
                "__temporal_activity_definition",
            )
        ]

    @staticmethod
    @activity.defn
    async def run_action(input: UDFActionInput[ArgsT]) -> Any:
        """Run an action.
        Goals:
        - Think of this as a controller activity that will orchestrate the execution of the action.
        - The implementation of the action is located elsewhere (registry service on API)
        """
        ctx_run.set(input.run_context)
        ctx_role.set(input.role)
        task = input.task

        act_logger = logger.bind(
            task_ref=task.ref, wf_id=input.run_context.wf_id, role=input.role
        )
        env_context = DSLEnvironment(**input.exec_context[ExprContext.ENV])

        try:
            # Multi-phase expression resolution
            # ---------------------------------
            # 1. Resolve all expressions in all shared (non action-local) contexts
            # 2. Enter loop iteration (if any)
            # 3. Resolve all action-local expressions

            # Set
            # If there's a for loop, we need to process this action in parallel

            # Evaluate `SECRETS` context (XXX: You likely should use the secrets manager instead)
            # --------------------------
            # Securely inject secrets into the task arguments
            # 1. Find all secrets in the task arguments
            # 2. Load the secrets
            # 3. Inject the secrets into the task arguments using an enriched context
            # NOTE: Regardless of loop iteration, we should only make this call/substitution once!!
            secret_refs = extract_templated_secrets(task.args)
            async with AuthSandbox(
                secrets=secret_refs,
                target="context",
                environment=env_context["environment"],
            ) as sandbox:
                secrets = sandbox.secrets.copy()
            context_with_secrets = {
                **input.exec_context,
                ExprContext.SECRETS: secrets,
            }

            if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
                act_logger.warning(
                    "Secrets masking is disabled. This is unsafe in production workflows."
                )
                mask_values = None
            else:
                # Safety: Secret context leaves are all strings
                mask_values = {s for _, s in traverse_leaves(secrets)}

            # When we're here, we've populated the task arguments with shared context values
            action_name = task.action
            ctx_logger.set(act_logger)

            # NOTE: Replace with REST call
            registry = RegistryManager().get_registry(input.registry_version)
            udf = registry[action_name]
            act_logger.info(
                "Run udf",
                task_ref=task.ref,
                action_name=action_name,
                is_async=udf.is_async,
                args=task.args,
            )

            # Short circuit if mocking the output
            if (act_test := input.action_test) and act_test.enable:
                # XXX: This will fail if we run it against a loop
                act_logger.warning(
                    f"Action test enabled, mocking the output of {task.ref!r}."
                    " You should not use this in production workflows."
                )
                if act_test.validate_args:
                    args = _evaluate_templated_args(task, context_with_secrets)
                    udf.validate_args(**args)
                return await resolve_success_output(act_test)

            # Actual execution

            if task.for_each:
                iterator = iter_for_each(task=task, context=context_with_secrets)
                try:
                    async with GatheringTaskGroup() as tg:
                        for patched_args in iterator:
                            tg.create_task(
                                udf.run_async(
                                    args=patched_args,
                                    context=context_with_secrets,
                                    registry=registry,
                                )
                            )

                    result = tg.results()
                except* Exception as eg:
                    errors = [str(x) for x in eg.exceptions]
                    logger.error("Error resolving expressions", errors=errors)
                    raise TracecatException(
                        (
                            f"[{context_locator(task, 'for_each')}]"
                            "\n\nError in loop:"
                            f"\n\n{'\n\n'.join(errors)}"
                        ),
                        detail={"errors": errors},
                    ) from eg

            else:
                args = _evaluate_templated_args(task, context_with_secrets)
                result = await udf.run_async(
                    args=args, context=context_with_secrets, registry=registry
                )

            if mask_values:
                result = apply_masks_object(result, masks=mask_values)

            act_logger.debug("Result", result=result)
            return result

        except TracecatException as e:
            err_type = e.__class__.__name__
            msg = _contextualize_message(task, e)
            act_logger.error(
                "Application exception occurred", error=msg, detail=e.detail
            )
            raise ApplicationError(
                msg, e.detail, non_retryable=True, type=err_type
            ) from e
        except httpx.HTTPStatusError as e:
            act_logger.error("HTTP status error occurred", error=e)
            raise ApplicationError(
                _contextualize_message(
                    task, f"HTTP status error {e.response.status_code}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except httpx.ReadTimeout as e:
            act_logger.error("HTTP read timeout occurred", error=e)
            raise ApplicationError(
                _contextualize_message(task, "HTTP read timeout"),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except ApplicationError as e:
            act_logger.error("ApplicationError occurred", error=e)
            raise ApplicationError(
                _contextualize_message(task, e.message),
                non_retryable=e.non_retryable,
                type=e.type,
            ) from e
        except Exception as e:
            act_logger.error("Unexpected error occurred", error=e)
            raise ApplicationError(
                _contextualize_message(
                    task, f"Unexpected error {e.__class__.__name__}: {e}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e


def _contextualize_message(
    task: ActionStatement[ArgsT], msg: str | BaseException, *, loc: str = "run_udf"
) -> str:
    return f"[{context_locator(task, loc)}]\n\n{msg}"


def iter_for_each(
    task: ActionStatement[ArgsT],
    context: DSLContext,
    *,
    assign_context: ExprContext = ExprContext.LOCAL_VARS,
    patch: bool = True,
) -> Iterator[ArgsT]:
    """Yield patched contexts for each loop iteration."""
    # Evaluate the loop expression
    iterators = get_iterables_from_expression(expr=task.for_each, operand=context)

    # Assert that all length of the iterables are the same
    # This is a requirement for parallel processing
    # if len({len(expr.collection) for expr in iterators}) != 1:
    #     raise ValueError("All iterables must be of the same length")

    # Create a generator that zips the iterables together
    for i, items in enumerate(zip(*iterators, strict=False)):
        logger.trace("Loop iteration", iteration=i)
        # Patch the context with the loop item and evaluate the action-local expressions
        # We're copying this so that we don't pollute the original context
        # Currently, the only source of action-local expressions is the loop iteration
        # In the future, we may have other sources of action-local expressions
        patched_context = (
            context.copy()
            if patch
            # XXX: ENV is the only context that should be shared
            else DSLContext.create_default()
        )
        logger.trace("Context before patch", patched_context=patched_context)
        for iterator_path, iterator_value in items:
            patch_object(
                patched_context,
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        logger.trace("Patched context", patched_context=patched_context)
        patched_args = _evaluate_templated_args(task=task, context=patched_context)
        logger.trace("Patched args", patched_args=patched_args)
        yield patched_args


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


def _validate_action_args(task: ActionStatement[ArgsT], registry_version: str) -> ArgsT:
    registry = RegistryManager().get_registry(registry_version)
    udf = registry.get(task.action)
    res = cast(ArgsT, udf.validate_args(**task.args))
    return res


def _evaluate_templated_args(
    task: ActionStatement[ArgsT], context: DSLContext
) -> ArgsT:
    return cast(ArgsT, eval_templated_object(task.args, operand=context))
