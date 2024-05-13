from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, TypedDict

from temporalio import workflow

from tracecat.experimental.dsl.activities import DSLActivities

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    from pydantic import BaseModel, ConfigDict, Field

    from tracecat.experimental.templates.eval import eval_templated_object


class _DSLNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class DSLInput(_DSLNode):
    root: Statement
    variables: dict[str, Any] = Field(default_factory=dict)


SLUG_PATTERN = r"^[a-z0-9_]+$"


class ActivityStatement(_DSLNode):
    ref: str = Field(pattern=SLUG_PATTERN)
    action: str = Field(pattern=SLUG_PATTERN)
    # Lower snake case slug is used for the key
    args: dict[str, Any] = Field(default_factory=dict)


class SequentialStatement(_DSLNode):
    sequence: list[Statement]


class ParallelStatement(_DSLNode):
    parallel: list[Statement]


Statement = ActivityStatement | SequentialStatement | ParallelStatement


class DSLContext(TypedDict):
    INPUTS: dict[str, Any]
    ACTIONS: dict[str, Any]


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str


@workflow.defn
class DSLWorkflow:
    @workflow.run
    async def run(self, input: DSLInput) -> DSLContext:
        # NOTE: This variable is like our action_result_store in the previous world
        self.context = DSLContext(INPUTS=input.variables, ACTIONS={})
        wfr_id = workflow.info().run_id
        workflow.logger.info(f"Running DSL workflow {wfr_id}")
        await self.execute_statement(input.root)
        workflow.logger.info("DSL workflow completed")
        return self.context

    async def execute_statement(self, stmt: Statement) -> None:
        match stmt:
            case ActivityStatement(ref=ref, action=action, args=args):
                # Resolve all templated arguments
                processed_args = eval_templated_object(args, operand=self.context)

                # Validate the arguments
                validator = DSLActivities.validators[action]
                # We use strict=True to ensure that no type coercion is done
                validated_args = validator.model_validate(processed_args, strict=True)
                activity_result = await workflow.execute_activity(
                    action,
                    arg=validated_args,
                    start_to_close_timeout=timedelta(minutes=1),
                )
                self.context["ACTIONS"][ref] = DSLNodeResult(
                    result=activity_result,
                    result_typename=type(activity_result).__name__,
                )
            case SequentialStatement(sequence=sequence):
                # Execute each statement in order
                for elem in sequence:
                    await self.execute_statement(elem)
            case ParallelStatement(parallel=parallel):
                # Execute all in parallel.
                # Note that using a TaskGroup makes it fail fast if any fail.
                async with asyncio.TaskGroup() as tg:
                    for branch in parallel:
                        tg.create_task(self.execute_statement(branch))
            case _:
                raise NotImplementedError(f"Unsupported statement: {stmt}")
