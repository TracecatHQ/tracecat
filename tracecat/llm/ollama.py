"""Core LLM functionality."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from typing import Any

import ollama
from ollama import ChatResponse

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.logger import logger


class OllamaModel(StrEnum):
    # Smol models
    LLAMA32 = "llama3.2"
    LLAMA32_1B = "llama3.2:1b"
    MISTRAL_SMALL = "mistral-small:24b"
    MIXTRAL = "mixtral"
    # Large models
    LLAMA33 = "llama3.3:70b"
    MISTRAL_LARGE = "mistral-large:7b"


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


async def async_ollama_call(
    prompt: str,
    *,
    model: OllamaModel,
    system_prompt: str | None = None,
    memory: list[dict[str, Any]] | None = None,
    stream: bool = False,
) -> AsyncIterator[ChatResponse] | ChatResponse:
    client = _get_ollama_client()

    if not await is_local_model(model):
        logger.warning(
            "Local LLM model not found",
            provider="ollama",
            model=model,
        )
        raise ValueError(f"Local LLM model {model!r} not found")

    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}]
    elif memory:
        messages = memory
    else:
        messages = [{"role": "user", "content": prompt}]

    logger.info(
        "🧠 Calling LLM chat",
        provider="ollama",
        model=model,
        prompt=prompt,
    )
    if stream:
        response = await client.chat(
            model=model,
            messages=messages,
            stream=stream,
        )
    else:
        response = await client.chat(
            model=model,
            messages=messages,
            stream=stream,
        )
    return response
