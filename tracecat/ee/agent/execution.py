from __future__ import annotations

from temporalio.client import Client, WorkflowHandle

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.ee.agent.models import (
    GraphAgentWorkflowArgs,
    GraphAgentWorkflowResult,
)
from tracecat.ee.agent.workflow import GraphAgentWorkflow
from tracecat.logger import logger
from tracecat.types.auth import Role


class AgentExecutionService:
    """Service to manage agentic execution."""

    service_name = "agent_execution"

    def __init__(self, client: Client, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service=self.service_name)

    @staticmethod
    async def connect(role: Role | None = None) -> AgentExecutionService:
        """Initialize and connect to the service."""
        client = await get_temporal_client()
        return AgentExecutionService(client=client, role=role)

    async def run_agent(self, args: GraphAgentWorkflowArgs) -> GraphAgentWorkflowResult:
        """Run an agentic turn and wait for it to complete."""
        handle = await self.start_agent(args)
        result = await handle.result()
        return GraphAgentWorkflowResult.model_validate(result)

    async def start_agent(
        self, args: GraphAgentWorkflowArgs
    ) -> WorkflowHandle[GraphAgentWorkflow, GraphAgentWorkflowResult]:
        """Start an agentic turn without waiting for it to complete."""
        handle = await self._client.start_workflow(
            GraphAgentWorkflow.run,
            args,
            id=args.session_id,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        return handle
