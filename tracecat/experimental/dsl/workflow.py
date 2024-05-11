from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field


class _DSLNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class DSLInput(_DSLNode):
    root: Statement
    variables: dict[str, Any] = Field(default_factory=dict)


class ActivityStatement(_DSLNode):
    action: str
    arguments: list[str] = Field(default_factory=list)
    result: str | None = None


class SequenceStatement(_DSLNode):
    sequence: list[Statement]


class ParallelStatement(_DSLNode):
    parallel: list[Statement]


Statement = ActivityStatement | SequenceStatement | ParallelStatement


@workflow.defn
class DSLWorkflow:
    @workflow.run
    async def run(self, input: DSLInput) -> dict[str, Any]:
        self.variables = dict(input.variables)
        workflow.logger.info("Running DSL workflow")
        await self.execute_statement(input.root)
        workflow.logger.info("DSL workflow completed")
        return self.variables

    async def execute_statement(self, stmt: Statement) -> None:
        match stmt:
            case ActivityStatement(
                action=action, arguments=arguments, result=stmt_result
            ):
                # Invoke activity loading arguments from variables and optionally
                # storing result as a variable
                activity_result = await workflow.execute_activity(
                    action,
                    args=[self.variables.get(arg, "") for arg in arguments],
                    start_to_close_timeout=timedelta(minutes=1),
                )
                if stmt_result:
                    self.variables[stmt_result] = activity_result
            case SequenceStatement(sequence=sequence):
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
