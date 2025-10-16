from __future__ import annotations as _annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from pydantic_ai import RequestUsage, RunContext, UserError
from pydantic_ai.durable_exec.temporal import TemporalRunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ModelResponseStreamEvent,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import AgentDepsT
from pydantic_core import to_json
from temporalio import workflow

from tracecat.agent.activities import AgentActivities, RequestStreamArgs
from tracecat.agent.context import AgentContext
from tracecat.agent.models import (
    ModelInfo,
    ModelRequestArgs,
    ModelResponseTA,
)
from tracecat.logger import logger
from tracecat.types.auth import Role


class TemporalStreamedResponse(StreamedResponse):
    def __init__(
        self, model_request_parameters: ModelRequestParameters, response: ModelResponse
    ):
        super().__init__(model_request_parameters=model_request_parameters)
        self.response = response

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        return
        # noinspection PyUnreachableCode
        yield

    def get(self) -> ModelResponse:
        return self.response

    def usage(self) -> RequestUsage:
        return self.response.usage  # pragma: no cover

    @property
    def model_name(self) -> str:
        return self.response.model_name or ""  # pragma: no cover

    @property
    def provider_name(self) -> str:
        return self.response.provider_name or ""  # pragma: no cover

    @property
    def timestamp(self) -> datetime:
        return self.response.timestamp  # pragma: no cover


class DurableModel(Model):
    """
    A durable AI model implementation that proxies model requests to Temporal activities.

    This class wraps AI model requests in Temporal activities to provide durability,
    retry capabilities, and workflow integration. All model requests are executed
    as Temporal activities, allowing them to be retried, monitored, and managed
    within the Temporal workflow system.
    """

    def __init__(
        self,
        info: ModelInfo,
        role: Role,
        *,
        activity_config: workflow.ActivityConfig,
        deps_type: type[AgentDepsT],
        run_context_type: type[TemporalRunContext[AgentDepsT]] = TemporalRunContext[
            AgentDepsT
        ],
    ):
        self._info = info
        self.role = role
        self.activity_config = activity_config
        self.deps_type = deps_type
        self.run_context_type = run_context_type

    @property
    def model_name(self) -> str:
        return self._info.name

    @property
    def system(self) -> str:
        return self._info.provider

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if not workflow.in_workflow():
            raise UserError("DurableModel request must be called inside a workflow")
        agent_ctx = AgentContext.get()
        args = ModelRequestArgs(
            role=self.role,
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            model_info=self._info,
        )
        logger.debug(f"DurableModel request: {to_json(args, indent=2).decode()}")
        result = await workflow.execute_activity_method(
            AgentActivities.model_request,
            args=(args, agent_ctx),
            start_to_close_timeout=timedelta(seconds=120),
        )
        resp = ModelResponseTA.validate_python(result.model_response)
        logger.debug(f"DurableModel response: {to_json(resp, indent=2).decode()}")
        return resp

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        if not workflow.in_workflow():
            raise UserError(
                "DurableModel request_stream must be called inside a workflow"
            )

        if run_context is None:
            raise UserError(
                "A Temporal model cannot be used with `pydantic_ai.direct.model_request_stream()` as it requires a `run_context`. Set an `event_stream_handler` on the agent and use `agent.run()` instead."
            )

        serialized_run_context = self.run_context_type.serialize_run_context(
            run_context
        )
        response = await workflow.execute_activity_method(
            AgentActivities.request_stream,
            args=[
                RequestStreamArgs(
                    role=self.role,
                    messages=messages,
                    model_settings=model_settings,
                    model_request_parameters=model_request_parameters,
                    serialized_run_context=serialized_run_context,
                    model_info=self._info,
                ),
                run_context.deps,
            ],
            **self.activity_config,
        )
        yield TemporalStreamedResponse(model_request_parameters, response)
