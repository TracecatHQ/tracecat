import asyncio
from typing import Any, TypedDict

from pydantic_ai.durable_exec.temporal import AgentPlugin, PydanticAIPlugin
from temporalio import workflow
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from tracecat import config
    from tracecat import config as oss_config
    from tracecat.agent.workflows.hitl import (
        HitlAgentWorkflow,
        hitl_temporal_agent,
    )
    from tracecat.dsl.client import get_temporal_client
    from tracecat.logger import logger


interrupt_event = asyncio.Event()


class WorkerConfig(TypedDict):
    task_queue: str
    workflows: list[type[Any]]
    plugins: list[AgentPlugin]
    disable_eager_activity_execution: bool


async def main() -> None:
    client = await get_temporal_client(plugins=[PydanticAIPlugin()])
    # Run a worker for the activities and workflow

    logger.info(
        "WorkerConfig", hitl_temporal_agent_model_id=id(hitl_temporal_agent.model)
    )
    cfg = WorkerConfig(
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        workflows=[HitlAgentWorkflow],
        plugins=[AgentPlugin(hitl_temporal_agent)],
        disable_eager_activity_execution=oss_config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
    )

    async with Worker(client, **cfg):
        logger.info(
            "Worker started, ctrl+c to exit",
            disable_eager_activity_execution=cfg["disable_eager_activity_execution"],
            task_queue=cfg["task_queue"],
            workflows=[w.__name__ for w in cfg["workflows"]],
            plugins=[p.agent.name for p in cfg["plugins"]],
        )
        # Wait until interrupted
        await interrupt_event.wait()
        logger.info("Shutting down")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
        loop.run_until_complete(loop.shutdown_asyncgens())
