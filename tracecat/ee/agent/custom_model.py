from __future__ import annotations as _annotations

from datetime import timedelta

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_core import to_json
from temporalio import workflow

from tracecat.contexts import ctx_role
from tracecat.ee.agent.activities import durable_model_request
from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ModelInfo,
    ModelResponseTA,
)
from tracecat.logger import logger


class DurableModel(Model):
    """A durable AI model implementation that proxies model requests to temporal activities.

    This class wraps AI model requests in Temporal activities to provide durability,
    retry capabilities, and workflow integration. All model requests are executed
    as Temporal activities, allowing them to be retried, monitored, and managed
    within the Temporal workflow system.
    """

    def __init__(self, info: ModelInfo):
        self._info = info

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
        role = ctx_role.get()
        args = DurableModelRequestArgs(
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            model_info=self._info,
        )
        logger.info(f"DurableModel request: {to_json(args, indent=2).decode()}")
        result = await workflow.execute_activity(
            durable_model_request,
            args=(args, role),
            start_to_close_timeout=timedelta(seconds=120),
        )
        resp = ModelResponseTA.validate_json(result.model_response)
        logger.info(f"DurableModel response: {to_json(resp, indent=2).decode()}")
        return resp
