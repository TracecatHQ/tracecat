"""Core LLM functionality."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import ollama

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.logger import logger


class OllamaModel(StrEnum):
    LLAMA32 = "llama3.2"
    LLAMA32_1B = "llama3.2:1b"


ModelType = OllamaModel


def _get_ollama_client() -> ollama.AsyncClient:
    return ollama.AsyncClient(host=config.OLLAMA__API_URL)


async def preload_ollama_models(models: list[str]) -> list[Mapping[str, Any]]:
    client = _get_ollama_client()
    async with GatheringTaskGroup() as tg:
        for model in models:
            tg.create_task(client.pull(model))
    return tg.results()


async def list_local_models() -> list[dict[str, Any]]:
    """List all models available locally."""
    client = _get_ollama_client()
    models = await client.list()
    return models["models"]


async def list_local_model_names() -> list[str]:
    """List all model names available locally."""
    models = await list_local_models()
    return [model["name"] for model in models]


async def is_local_model(model: str) -> bool:
    """Check if a model is available locally."""
    return model in await list_local_model_names()


async def async_ollama_call(*, prompt: str, model: OllamaModel) -> Mapping[str, Any]:
    client = _get_ollama_client()

    if not await is_local_model(model):
        logger.warning(
            "Local LLM model not found",
            provider="ollama",
            model=model,
        )
        raise ValueError(f"Local LLM model {model!r} not found")

    logger.info(
        "🧠 Calling LLM chat",
        provider="ollama",
        model=model,
        prompt=prompt,
    )
    response = await client.chat(
        model=model, messages=[{"role": "user", "content": prompt}]
    )
    return response
