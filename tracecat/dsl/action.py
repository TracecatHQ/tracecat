from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dateparser
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger, ctx_run
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.common import RunTableInsertRowArgs, RunTableLookupArgs
from tracecat.dsl.enums import CoreActions
from tracecat.dsl.models import ActionErrorInfo, ActionStatement, RunActionInput
from tracecat.executor.client import ExecutorClient
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.tables.models import TableRowInsert
from tracecat.tables.service import TablesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import ExecutorClientError, RegistryError
from tracecat.validation.service import validate_registry_action_args


class ValidateActionActivityInput(BaseModel):
    role: Role
    task: ActionStatement


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[RunActionInput], Any]]:
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
    async def validate_action_activity(
        input: ValidateActionActivityInput,
    ) -> RegistryActionValidateResponse:
        """Validate an action.
        Goals:
        - Validate the action arguments against the UDF spec.
        - Return the validated arguments.
        """
        try:
            async with get_async_session_context_manager() as session:
                result = await validate_registry_action_args(
                    session=session,
                    action_name=input.task.action,
                    args=input.task.args,
                )

                if result.status == "error":
                    logger.warning(
                        "Error validating UDF args",
                        message=result.msg,
                        details=result.detail,
                    )
                return RegistryActionValidateResponse.from_validation_result(result)
        except KeyError as e:
            raise RegistryError(
                f"Action {input.task.action!r} not found in registry",
            ) from e

    @staticmethod
    @activity.defn
    async def run_action_activity(input: RunActionInput, role: Role) -> Any:
        """Run an action.
        Goals:
        - Think of this as a controller activity that will orchestrate the execution of the action.
        - The implementation of the action is located elsewhere (registry service on API)
        """
        ctx_run.set(input.run_context)
        task = input.task
        environment = input.run_context.environment
        action_name = task.action

        act_logger = logger.bind(
            task_ref=task.ref,
            action_name=action_name,
            wf_id=input.run_context.wf_id,
            role=role,
            environment=environment,
        )
        ctx_logger.set(act_logger)

        act_info = activity.info()
        attempt = act_info.attempt
        act_logger.info(
            "Run action activity",
            task=task,
            attempt=attempt,
            retry_policy=task.retry_policy,
        )
        try:
            if task.action == CoreActions.TABLE_LOOKUP:
                # Do a table lookup
                resolved_args = eval_templated_object(
                    task.args, operand=input.exec_context
                )
                args = RunTableLookupArgs.model_validate(resolved_args)
                async with TablesService.with_session(role=role) as service:
                    rows = await service.lookup_row(
                        table_name=args.table,
                        columns=[args.column],
                        values=[args.value],
                    )
                return rows[0] if rows else None
            elif task.action == CoreActions.TABLE_INSERT_ROW:
                args = RunTableInsertRowArgs.model_validate(task.args)
                params = TableRowInsert(data=args.row_data)
                async with TablesService.with_session(role=role) as service:
                    table = await service.get_table_by_name(args.table)
                    row = await service.insert_row(table=table, params=params)
                return row
            else:
                # Run other actions in the executor
                client = ExecutorClient(role=role)
                return await client.run_action_memory_backend(input)
        except ExecutorClientError as e:
            # We only expect ExecutorClientError to be raised from the executor client
            kind = e.__class__.__name__
            msg = str(e)
            act_logger.error(
                "Application exception occurred", error=msg, detail=e.detail
            )
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=msg,
                type=kind,
                attempt=attempt,
            )
            err_msg = err_info.format("run_action")
            raise ApplicationError(err_msg, err_info, type=kind) from e
        except ApplicationError as e:
            # Unexpected application error - depends
            act_logger.error("ApplicationError occurred", error=e)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=str(e),
                type=e.type or e.__class__.__name__,
                attempt=attempt,
            )
            err_msg = err_info.format("run_action")
            raise ApplicationError(
                err_msg, err_info, non_retryable=e.non_retryable, type=e.type
            ) from e
        except Exception as e:
            # Unexpected errors - non-retryable
            kind = e.__class__.__name__
            raw_msg = f"{kind} occurred:\n{e}"
            act_logger.error(raw_msg)

            err_info = ActionErrorInfo(
                ref=task.ref,
                message=raw_msg,
                type=kind,
                attempt=attempt,
            )
            err_msg = err_info.format("run_action")
            raise ApplicationError(
                err_msg, err_info, type=kind, non_retryable=True
            ) from e

    @staticmethod
    @activity.defn
    async def parse_wait_until_activity(
        wait_until: str,
    ) -> str | None:
        """Parse the wait until datetime. We wrap this in an activity to avoid
        non-determinism errors when using the `dateparser` library
        """
        dt = dateparser.parse(
            wait_until, settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}
        )
        return dt.isoformat() if dt else None
