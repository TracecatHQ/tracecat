from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel
from pydantic_ai import ModelSettings, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from temporalio import activity

from tracecat.agent.providers import get_model


class ModelInfo(BaseModel):
    name: str
    provider: str
    base_url: str | None


class LazyModel(Model):
    """A model that lazily loads the model based on the context."""

    def __init__(self, model_info: ModelInfo | None = None):
        self._model_info = model_info

    @property
    def model_name(self) -> str:
        if not self._model_info:
            return "<lazy-model:unset>"
        return self._model_info.name

    @property
    def system(self) -> str:
        if not self._model_info:
            return "<lazy-model:unset>"
        return self._model_info.provider

    def set(self, model_info: ModelInfo) -> None:
        self._model_info = model_info

    def get(self) -> Model:
        if not self._model_info:
            raise RuntimeError("Model is not set")
        return get_model(
            self._model_info.name, self._model_info.provider, self._model_info.base_url
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if not activity.in_activity():
            raise RuntimeError("request should be called inside the activity")
        # This should be called inside the activity
        return await self.get().request(
            messages, model_settings, model_request_parameters
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        if not activity.in_activity():
            raise RuntimeError("request_stream should be called inside the activity")
        # This should be called inside the activity
        async with self.get().request_stream(
            messages, model_settings, model_request_parameters
        ) as response_stream:
            yield response_stream
