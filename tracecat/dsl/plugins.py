from __future__ import annotations

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.converter import DataConverter

from tracecat import config
from tracecat.dsl._converter import get_data_converter


class TracecatPydanticAIPlugin(PydanticAIPlugin):
    """Pydantic AI plugin that preserves Tracecat's custom data converter."""

    def _get_new_data_converter(self, converter: DataConverter | None) -> DataConverter:
        if converter is not None:
            return converter

        return get_data_converter(
            compression_enabled=config.TRACECAT__CONTEXT_COMPRESSION_ENABLED
        )
