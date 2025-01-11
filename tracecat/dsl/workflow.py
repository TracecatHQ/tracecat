from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from collections.abc import Generator, Iterable
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

from tracecat.dsl.models import RunContext

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.ext.parser  # noqa: F401
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    import tracecat_registry  # noqa
    from pydantic import TypeAdapter, ValidationError

    from tracecat import identifiers
    from tracecat.concurrency import GatheringTaskGroup
    from tracecat.contexts import ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.action import (
        DSLActivities,
        ValidateActionActivityInput,
    )
    from tracecat.dsl.common import (
        DSLInput,
        DSLRunArgs,
        ExecuteChildWorkflowArgs,
        dsl_execution_error_from_exception,
    )
    from tracecat.dsl.constants import CHILD_WORKFLOW_EXECUTE_ACTION
    from tracecat.dsl.enums import FailStrategy, LoopStrategy
    from tracecat.dsl.models import (
        ActionErrorInfo,
        ActionStatement,
        DSLConfig,
        DSLEnvironment,
        DSLExecutionError,
        DSLNodeResult,
        ExecutionContext,
        RunActionInput,
        TriggerInputs,
    )
    from tracecat.dsl.scheduler import DSLScheduler
    from tracecat.dsl.validation import (
        ValidateTriggerInputsActivityInputs,
        validate_trigger_inputs_activity,
    )
    from tracecat.executor.service import evaluate_templated_args, iter_for_each
    from tracecat.expressions.common import ExprContext
    from tracecat.expressions.eval import eval_templated_object
    from tracecat.identifiers.workflow import WorkflowExecutionID, WorkflowID
    from tracecat.logger import logger
    from tracecat.types.exceptions import (
        TracecatCredentialsError,
        TracecatDSLError,
        TracecatException,
        TracecatExpressionError,
        TracecatNotFoundError,
        TracecatValidationError,
    )
    from tracecat.validation.models import ValidationResult
    from tracecat.workflow.executions.models import ErrorHandlerWorkflowInput
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
    )
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.models import (
        GetErrorHandlerWorkflowIDActivityInputs,
        GetWorkflowDefinitionActivityInputs,
        ResolveWorkflowAliasActivityInputs,
    )
    from tracecat.workflow.schedules.models import GetScheduleActivityInputs
    from tracecat.workflow.schedules.service import WorkflowSchedulesService


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


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow."""

    @workflow.run
    async def run(self, args: DSLRunArgs) -> Any:
        self.role = args.role
        self.start_to_close_timeout = args.timeout
        wf_info = workflow.info()
        wf_exec_id = wf_info.workflow_id
        wf_run_id = wf_info.run_id
        self.logger = logger.bind(
            wf_id=args.wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            role=self.role,
            service="dsl-workflow-runner",
        )
        # Set runtime args
        ctx_role.set(self.role)
        ctx_logger.set(self.logger)

        self.logger.debug("DSL workflow started", args=args)
        try:
            self.logger.info(
                "Workflow info",
                run_timeout=wf_info.run_timeout,
                execution_timeout=wf_info.execution_timeout,
                task_timeout=wf_info.task_timeout,
                retry_policy=wf_info.retry_policy,
                history_events_length=wf_info.get_current_history_length(),
                history_events_size_bytes=wf_info.get_current_history_size(),
            )
        except Exception as e:
            self.logger.error("Failed to show workflow info", error=e)

        # Set DSL

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

        # Note that we can't run the error handler above this
        # Run the workflow with error handling
        try:
            return await self._run_workflow(args)
        except ApplicationError as e:
            # Application error
            self.logger.warning(
                "Error running workflow, running error handler",
                type=e.__class__.__name__,
            )
            # 1. Get the error handler workflow ID
            handler_wf_id = await self._get_error_handler_workflow_id(args)
            if not handler_wf_id:
                self.logger.warning("No error handler workflow ID found, raising error")
                raise e

            if e.details:
                err_info_map = e.details[0]
                self.logger.info("Raising error info", err_info_data=err_info_map)
                ta = TypeAdapter(ActionErrorInfo)
                errors = {
                    ref: ta.validate_python(data) for ref, data in err_info_map.items()
                }
            else:
                errors = None

            try:
                err_run_args = await self._prepare_error_handler_workflow(
                    handler_wf_id,
                    message=e.message,
                    handler_wf_id=handler_wf_id,
                    orig_wf_id=args.wf_id,
                    orig_wf_exec_id=wf_exec_id,
                    errors=errors,
                )
                await self._run_error_handler_workflow(err_run_args)
            except Exception as err_handler_exc:
                self.logger.error(
                    "Failed to run error handler workflow",
                    error=err_handler_exc,
                )
                raise err_handler_exc from e

            # Finally, raise the original error
            raise e
        except Exception as e:
            # Platform error
            self.logger.error(
                "Unexpected error running workflow",
                type=e.__class__.__name__,
                error=e,
            )
            raise e

    async def _run_workflow(self, args: DSLRunArgs) -> Any:
        """Actual workflow execution logic."""
        wf_info = workflow.info()

        # Consolidate runtime config
        if "runtime_config" in args.model_fields_set:
            # XXX(warning): This section must be handled with care.
            # Particularly because of how Pydantic handles unset fields.
            # We allow incoming runtime config in args to override the DSL config.

            # Use the override runtime config if it's set
            # If we receive runtime config in args, we must
            # consolidate the args in this order:
            # 1. runtime_config.environment (override by caller)
            # 2. dsl.config.environment (set in wf defn)

            logger.warning(
                "Runtime config was set",
                args_config=args.runtime_config,
                dsl_config=self.dsl.config,
            )
            set_fields = args.runtime_config.model_dump(exclude_unset=True)
            self.runtime_config = self.dsl.config.model_copy(update=set_fields)
        else:
            # Otherwise default to the DSL config
            logger.warning(
                "Runtime config was not set, using DSL config",
                dsl_config=self.dsl.config,
            )
            self.runtime_config = self.dsl.config
        logger.warning("Runtime config after", runtime_config=self.runtime_config)

        # Consolidate trigger inputs
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

        # Prepare user facing context
        self.context: ExecutionContext = {
            ExprContext.ACTIONS: {},
            ExprContext.INPUTS: self.dsl.inputs,
            ExprContext.TRIGGER: trigger_inputs,
            ExprContext.ENV: DSLEnvironment(
                workflow={
                    "start_time": wf_info.start_time,
                    "dispatch_type": self.dispatch_type,
                },
                environment=self.runtime_config.environment,
                variables={},
            ),
        }

        # All the starting config has been consolidated, can safely set the run context
        # Internal facing context
        self.run_context = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=uuid.UUID(wf_info.run_id, version=4),
            environment=self.runtime_config.environment,
        )
        ctx_run.set(self.run_context)

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}

        self.logger.info(
            "Running DSL task workflow",
            runtime_config=self.runtime_config,
            timeout=self.start_to_close_timeout,
        )

        self.scheduler = DSLScheduler(
            executor=self.execute_task,  # type: ignore
            dsl=self.dsl,
            context=self.context,
        )
        try:
            task_exceptions = await self.scheduler.start()
        except Exception as e:
            msg = f"DSL scheduler failed with unexpected error: {e}"
            raise ApplicationError(
                msg, non_retryable=True, type=e.__class__.__name__
            ) from e

        if task_exceptions:
            n_exc = len(task_exceptions)
            formatted_exc = "\n".join(
                f"{'=' * 20} ({i + 1}/{n_exc}) {details.expr_context}.{ref} {'=' * 20}\n\n{info.exception!s}"
                for i, (ref, info) in enumerate(task_exceptions.items())
                if (details := info.details)
            )
            # NOTE: This error is shown in the final activity in the workflow history
            raise ApplicationError(
                f"Workflow failed with {n_exc} task exception(s)\n\n{formatted_exc}",
                # We should add the details of the exceptions to the error message because this will get captured
                # in the error handler workflow
                {ref: info.details for ref, info in task_exceptions.items()},
                non_retryable=True,
                type=ApplicationError.__name__,
            )

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

    async def execute_task(self, task: ActionStatement) -> Any:
        """Purely execute a task and manage the results.

        Preflight checks
        ---------------
        1. Evaluate `run_if` condition
        2. Resolve all templated arguments
        3. If there's an ActionTest, skip execution and return the patched result.
            - Note that we still schedule the task for execution, but we don't actually run it.
        """

        logger.info("Begin task execution", task_ref=task.ref)
        task_result = DSLNodeResult(result=None, result_typename=type(None).__name__)
        try:
            if self._should_execute_child_workflow(task):
                # NOTE: We don't support (nor recommend, unless a use case is justified) passing SECRETS to child workflows
                # 1. Prepare the child workflow
                logger.trace("Preparing child workflow")
                child_run_args = await self._prepare_child_workflow(task)
                logger.trace("Child workflow prepared", child_run_args=child_run_args)
                # This is the original child runtime args, preset by the DSL
                # In contrast, task.args are the runtime args that the parent workflow provided
                action_result = await self._execute_child_workflow(
                    task=task, child_run_args=child_run_args
                )

            else:
                # Below this point, we're executing the task
                logger.trace(
                    "Running action",
                    task_ref=task.ref,
                    runtime_config=self.runtime_config,
                )
                action_result = await self._run_action(task)
            logger.trace("Action completed successfully", action_result=action_result)
            task_result.update(
                result=action_result, result_typename=type(action_result).__name__
            )
        # NOTE: By the time we receive an exception, we've exhausted all retry attempts
        # Note that execute_task is called by the scheduler, so we don't have to return ApplicationError
        except (ActivityError, ChildWorkflowError, FailureError) as e:
            # These are deterministic and expected errors that
            err_type = e.__class__.__name__
            msg = self.ERROR_TYPE_TO_MESSAGE[err_type]
            self.logger.error(msg, role=self.role, e=e, cause=e.cause, type=err_type)
            match cause := e.cause:
                case ApplicationError(details=details) if details:
                    err_info = details[0]
                    err_type = cause.type or err_type
                    task_result.update(error=err_info, error_typename=err_type)
                    # Reraise the cause, as it's wrapped by the ApplicationError
                    raise cause from e
                case _:
                    self.logger.warning("Unexpected error cause", cause=cause)
                    task_result.update(error=e.message, error_typename=err_type)
                    raise ApplicationError(
                        e.message, non_retryable=True, type=err_type
                    ) from cause

        except TracecatExpressionError as e:
            err_type = e.__class__.__name__
            detail = e.detail or "Error occurred when handling an expression"
            raise ApplicationError(detail, non_retryable=True, type=err_type) from e

        except ValidationError as e:
            logger.error("Runtime validation error", error=e.errors())
            task_result.update(
                error=e.errors(), error_typename=ValidationError.__name__
            )
            raise e
        except Exception as e:
            err_type = e.__class__.__name__
            msg = f"Task execution failed with unexpected error: {e}"
            logger.error(
                "Activity execution failed with unexpected error",
                error=msg,
                type=err_type,
            )
            task_result.update(error=msg, error_typename=err_type)
            raise ApplicationError(msg, non_retryable=True, type=err_type) from e
        finally:
            logger.debug("Setting action result", task_result=task_result)
            self.context[ExprContext.ACTIONS][task.ref] = task_result  # type: ignore

    ERROR_TYPE_TO_MESSAGE = {
        ActivityError.__name__: "Activity execution failed",
        ChildWorkflowError.__name__: "Child workflow execution failed",
        FailureError.__name__: "Workflow execution failed",
        ValidationError.__name__: "Runtime validation error",
    }

    async def _execute_child_workflow(
        self,
        task: ActionStatement,
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
            args = evaluate_templated_args(task, context=self.context)
            self.logger.trace(
                "Executing child workflow",
                child_run_args=child_run_args,
                task_args=task.args,
                evaluated_args=args,
            )

            if child_run_args.dsl is None:
                raise ValueError("Child run args must have a DSL")
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
        task: ActionStatement,
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

        def iterator() -> Generator[ExecuteChildWorkflowArgs]:
            for args in iter_for_each(task=task, context=self.context):
                yield ExecuteChildWorkflowArgs(**args)

        batch_size = {
            LoopStrategy.SEQUENTIAL: 1,
            LoopStrategy.BATCH: int(task.args.get("batch_size") or 16),
            LoopStrategy.PARALLEL: 16,
        }[loop_strategy]

        action_result = []
        for batch in itertools.batched(iterator(), batch_size):
            batch_result = await self._execute_child_workflow_batch(
                batch=batch,
                base_run_args=child_run_args,
                fail_strategy=fail_strategy,
            )
            action_result.extend(batch_result)
        return action_result

    async def _execute_child_workflow_batch(
        self,
        batch: Iterable[ExecuteChildWorkflowArgs],
        base_run_args: DSLRunArgs,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[Any]:
        def iter_patched_args() -> Generator[DSLRunArgs]:
            for args in batch:
                cloned_args = base_run_args.model_copy()
                cloned_args.trigger_inputs = args.trigger_inputs
                cloned_args.runtime_config = base_run_args.runtime_config.model_copy()
                cloned_args.runtime_config.environment = (
                    args.environment or base_run_args.runtime_config.environment
                )
                cloned_args.runtime_config.timeout = (
                    args.timeout or base_run_args.runtime_config.timeout
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
                dsl_execution_error_from_exception(val)
                if isinstance(val, BaseException)
                else val
                for val in gather_result
            ]
            return result

    def _handle_return(self) -> Any:
        self.logger.debug("Handling return", context=self.context)
        if self.dsl.returns is None:
            # Return the context
            # XXX: Don't return ENV context for now
            self.logger.trace("Returning DSL context")
            self.context.pop(ExprContext.ENV, None)
            return self.context
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        return eval_templated_object(self.dsl.returns, operand=self.context)

    async def _resolve_workflow_alias(self, wf_alias: str) -> identifiers.WorkflowID:
        activity_inputs = ResolveWorkflowAliasActivityInputs(
            workflow_alias=wf_alias, role=self.role
        )
        wf_id = await workflow.execute_activity(
            WorkflowsManagementService.resolve_workflow_alias_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        if not wf_id:
            raise ValueError(f"Workflow alias {wf_alias} not found")
        return wf_id

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
        self, trigger_inputs: TriggerInputs
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
    ) -> dict[str, Any] | None:
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

    async def _validate_action(self, task: ActionStatement) -> None:
        result = await workflow.execute_activity(
            DSLActivities.validate_action_activity,
            arg=ValidateActionActivityInput(role=self.role, task=task),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        if not result.ok:
            raise ApplicationError(
                f"Action validation failed: {result.message}",
                result.detail,
                non_retryable=True,
                type=TracecatValidationError.__name__,
            )

    async def _prepare_child_workflow(self, task: ActionStatement) -> DSLRunArgs:
        """Grab a workflow definition and create child workflow run args"""

        args = ExecuteChildWorkflowArgs.model_validate(task.args)
        # If wfid already exists don't do anything
        # Before we execute the child workflow, resolve the workflow alias
        # environment is None here. This is coming from the action
        self.logger.trace("Validated child workflow args", task=task)

        if args.workflow_id:
            child_wf_id = args.workflow_id
        elif args.workflow_alias:
            child_wf_id = await self._resolve_workflow_alias(args.workflow_alias)
        else:
            raise ValueError("Either workflow_id or workflow_alias must be provided")

        dsl = await self._get_workflow_definition(child_wf_id, version=args.version)

        self.logger.debug(
            "Got workflow definition",
            dsl=dsl,
            args=args,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )
        runtime_config = DSLConfig(
            # Override the environment in the runtime config,
            # otherwise use the default provided in the workflow definition
            environment=args.environment or dsl.config.environment,
            timeout=args.timeout or dsl.config.timeout,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=child_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=args.trigger_inputs,
            runtime_config=runtime_config,
        )

    async def _run_action(self, task: ActionStatement) -> Any:
        arg = RunActionInput(
            task=task, run_context=self.run_context, exec_context=self.context
        )
        self.logger.debug("RUN UDF ACTIVITY", arg=arg)

        return await workflow.execute_activity(
            DSLActivities.run_action_activity,
            args=(arg, self.role),
            start_to_close_timeout=timedelta(
                seconds=task.start_delay + task.retry_policy.timeout
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=task.retry_policy.max_attempts,
            ),
        )

    async def _run_child_workflow(self, run_args: DSLRunArgs) -> Any:
        self.logger.info("Running child workflow", run_args=run_args)
        wf_exec_id = identifiers.workflow.generate_exec_id(run_args.wf_id)
        wf_info = workflow.info()
        return await workflow.execute_child_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            retry_policy=retry_policies["workflow:fail_fast"],
            # Propagate the parent workflow attributes to the child workflow
            task_queue=wf_info.task_queue,
            execution_timeout=wf_info.execution_timeout,
            task_timeout=wf_info.task_timeout,
        )

    def _should_execute_child_workflow(self, task: ActionStatement) -> bool:
        return task.action == CHILD_WORKFLOW_EXECUTE_ACTION

    async def _get_error_handler_workflow_id(
        self, args: DSLRunArgs
    ) -> WorkflowID | None:
        """Get the error handler workflow ID.

        This is done by checking if the error is a TracecatValidationError or
        TracecatExpressionError.
        """
        return await workflow.execute_activity(
            WorkflowsManagementService.get_error_handler_workflow_id,
            arg=GetErrorHandlerWorkflowIDActivityInputs(args=args, role=self.role),
            start_to_close_timeout=args.timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    async def _prepare_error_handler_workflow(
        self,
        wf_id: WorkflowID,
        *,
        message: str,
        handler_wf_id: WorkflowID,
        orig_wf_id: WorkflowID,
        orig_wf_exec_id: WorkflowExecutionID,
        errors: dict[str, ActionErrorInfo] | None = None,
    ) -> DSLRunArgs:
        """Grab a workflow definition and create error handler workflow run args"""

        dsl = await self._get_workflow_definition(handler_wf_id)

        self.logger.debug(
            "Got workflow definition for error handler",
            dsl=dsl,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )
        runtime_config = DSLConfig(
            # Override the environment in the runtime config,
            # otherwise use the default provided in the workflow definition
            environment=self.runtime_config.environment,
            timeout=self.runtime_config.timeout,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=ErrorHandlerWorkflowInput(
                message=message,
                handler_wf_id=wf_id,
                orig_wf_id=orig_wf_id,
                orig_wf_exec_id=orig_wf_exec_id,
                errors=errors,
            ),
            runtime_config=runtime_config,
        )

    async def _run_error_handler_workflow(
        self,
        args: DSLRunArgs,
    ) -> None:
        self.logger.info("Running error handler workflow", args=args)
        wf_exec_id = identifiers.workflow.generate_exec_id(args.wf_id)
        wf_info = workflow.info()
        await workflow.execute_child_workflow(
            DSLWorkflow.run,
            args,
            id=wf_exec_id,
            retry_policy=retry_policies["workflow:fail_fast"],
            # Propagate the parent workflow attributes to the child workflow
            task_queue=wf_info.task_queue,
            execution_timeout=wf_info.execution_timeout,
            task_timeout=wf_info.task_timeout,
        )
