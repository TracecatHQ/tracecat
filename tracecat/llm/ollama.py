"""Ollama LLM client.

Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from typing import Any

import ollama
from async_lru import alru_cache
from ollama import ChatResponse
from pydantic import BaseModel

from tracecat.logger import logger


def _get_ollama_client(base_url: str) -> ollama.AsyncClient:
    return ollama.AsyncClient(host=base_url)


async def list_local_models(base_url: str) -> list[dict[str, Any]]:
    """List all models available locally."""
    client = _get_ollama_client(base_url)
    models = await client.list()
    return [model.model_dump() for model in models.models]


async def list_local_model_names(base_url: str) -> list[str | None]:
    """List all model names available locally."""
    models = await list_local_models(base_url)
    return [model["model"] for model in models]


@alru_cache(ttl=3600)
async def is_local_model(model: str, base_url: str) -> bool:
    """Check if a model is available locally."""
    return model in await list_local_model_names(base_url)


async def async_ollama_call(
    prompt: str,
    *,
    model: str,
    memory: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    format: dict[str, Any] | BaseModel | None = None,
    base_url: str,
) -> ChatResponse:
    client = _get_ollama_client(base_url)

    if not await is_local_model(model, base_url):
        logger.warning(
            "Local LLM model not found",
            provider="ollama",
            model=model,
        )
        raise ValueError(f"Local LLM model {model!r} not found")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if memory:
        messages.extend(memory)
    messages.append({"role": "user", "content": prompt})

    logger.debug(
        "🧠 Calling LLM chat completion",
        provider="ollama",
        model=model,
        prompt=prompt,
    )

    kwargs = {"model": model, "messages": messages}
    if format:
        if isinstance(format, BaseModel):
            kwargs["format"] = format.model_json_schema()
        else:
            kwargs["format"] = format

    response = await client.chat(**kwargs)
    return response
