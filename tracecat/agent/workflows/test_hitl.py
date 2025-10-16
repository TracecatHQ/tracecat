from __future__ import annotations

import asyncio
import uuid

from pydantic_ai.durable_exec.temporal import AgentPlugin, PydanticAIPlugin
from pydantic_ai.tools import DeferredToolResults
from temporalio.client import Client
from temporalio.worker import Worker

from tracecat.agent.activities import AgentActivities
from tracecat.agent.models import AgentConfig, RunAgentArgs
from tracecat.agent.stream.common import PersistableStreamingAgentDeps
from tracecat.agent.tools import SimpleToolExecutor
from tracecat.agent.workflows.durable import AgentWorkflowArgs, DurableAgentWorkflow
from tracecat.agent.workflows.hitl import (
    HitlAgentWorkflow,
    HitlAgentWorkflowArgs,
    hitl_temporal_agent,
)
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role

TEMPORAL_PORT = 7233
TASK_QUEUE = "pydantic-ai-agent-task-queue"


async def test_temporal_agent_with_hitl_tool():
    client = await Client.connect(
        f"localhost:{TEMPORAL_PORT}",
        plugins=[PydanticAIPlugin()],
    )

    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[HitlAgentWorkflow],
        plugins=[AgentPlugin(hitl_temporal_agent)],
    ) as worker:
        logger.info(
            "WorkerConfig", hitl_temporal_agent_model_id=id(hitl_temporal_agent.model)
        )
        logger.info("Worker started", config=worker.config().get("activities"))
        session_id = uuid.uuid4()
        workspace_id = uuid.uuid4()

        workflow = await client.start_workflow(
            HitlAgentWorkflow.run,
            HitlAgentWorkflowArgs(
                agent_args=RunAgentArgs(
                    user_prompt="What tools are available?",
                    session_id=session_id,
                    config=AgentConfig(
                        model_name="gpt-4o-mini",
                        model_provider="openai",
                    ),
                ),
                role=Role(
                    type="service",
                    service_id="tracecat-api",
                    workspace_id=workspace_id,
                    access_level=AccessLevel.ADMIN,
                ),
            ),
            id=str(uuid.uuid4()),
            task_queue=TASK_QUEUE,
        )
        while True:
            await asyncio.sleep(1)
            status = await workflow.query(HitlAgentWorkflow.get_status)
            if status == "done":
                break
            elif status == "waiting_for_results":
                deferred_tool_requests = await workflow.query(
                    HitlAgentWorkflow.get_deferred_tool_requests
                )
                assert deferred_tool_requests is not None

                results = DeferredToolResults()
                # Approve all calls
                for tool_call in deferred_tool_requests.approvals:
                    results.approvals[tool_call.tool_call_id] = True

                for tool_call in deferred_tool_requests.calls:
                    results.calls[tool_call.tool_call_id] = "Success"

                await workflow.signal(
                    HitlAgentWorkflow.set_deferred_tool_results, results
                )

        result = await workflow.result()
        print(result.output)
        print(result.all_messages())


async def test_temporal_agent_with_hitl_tool2():
    client = await Client.connect(
        f"localhost:{TEMPORAL_PORT}",
        plugins=[PydanticAIPlugin()],
    )

    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    deps = await PersistableStreamingAgentDeps.new(session_id, workspace_id)
    executor = SimpleToolExecutor()
    activities = AgentActivities(deps=deps, executor=executor)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DurableAgentWorkflow],
        activities=activities.all_activities(),
    ) as worker:
        logger.info(
            "WorkerConfig", hitl_temporal_agent_model_id=id(hitl_temporal_agent.model)
        )
        logger.info("Worker started", config=worker.config().get("activities"))

        print("\n=== Interactive Agent REPL ===")
        print("Type 'exit' or 'quit' to end the session\n")

        while True:
            try:
                user_prompt = input("You: ").strip()

                if user_prompt.lower() in ["exit", "quit"]:
                    print("Ending session...")
                    break

                if not user_prompt:
                    continue

                result = await client.execute_workflow(
                    DurableAgentWorkflow.run,
                    AgentWorkflowArgs(
                        agent_args=RunAgentArgs(
                            user_prompt=user_prompt,
                            session_id=session_id,
                            config=AgentConfig(
                                model_name="gpt-4o-mini",
                                model_provider="openai",
                                actions=[
                                    "core.cases.create_case",
                                    "core.cases.update_case",
                                ],
                            ),
                        ),
                        role=Role(
                            type="service",
                            service_id="tracecat-api",
                            workspace_id=workspace_id,
                            access_level=AccessLevel.ADMIN,
                        ),
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=TASK_QUEUE,
                )
                print(f"\nAgent: {result.output}\n")
            except KeyboardInterrupt:
                print("\n\nEnding session...")
                break
            except Exception as e:
                print(f"\nError: {e}\n")
                continue


if __name__ == "__main__":
    asyncio.run(test_temporal_agent_with_hitl_tool2())
