"""Ollama LLM client.

Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from enum import StrEnum
from typing import Any

import ollama
from async_lru import alru_cache
from ollama import ChatResponse
from pydantic import BaseModel

from tracecat import config
from tracecat.logger import logger


# NOTE: We specify the params size tag otherwise
# it defaults to the ambigious "latest" tag
# https://ollama.com/search
class OllamaModel(StrEnum):
    # Smol models (<15GB)
    LLAMA32 = "llama3.2:3b"
    LLAMA32_1B = "llama3.2:1b"
    MISTRAL_SMALL = "mistral-small:24b"
    # Large models
    LLAMA33 = "llama3.3:70b"
    MISTRAL_LARGE = "mistral-large:123b"
    MIXTRAL = "mixtral:8x7b"
    MIXTRAL_22B = "mixtral:8x22b"


DEFAULT_OLLAMA_MODEL = OllamaModel.LLAMA32_1B


def _get_ollama_client(api_url: str | None = None) -> ollama.AsyncClient:
    return ollama.AsyncClient(host=api_url or config.OLLAMA__API_URL)


async def preload_ollama_models(models: list[str]) -> list[dict[str, Any]]:
    client = _get_ollama_client()
    responses = []
    # Download iteratively to avoid overwhelming
    # the Ollama client and server
    for model in models:
        try:
            await client.pull(model)
        except Exception as e:
            logger.warning(
                "Failed to pull model",
                model=model,
                error=e,
            )
    return responses


async def list_local_models(api_url: str | None = None) -> list[dict[str, Any]]:
    """List all models available locally."""
    client = _get_ollama_client(api_url)
    models = await client.list()
    return [model.model_dump() for model in models.models]


async def list_local_model_names(api_url: str | None = None) -> list[str | None]:
    """List all model names available locally."""
    models = await list_local_models(api_url)
    return [model["model"] for model in models]


@alru_cache(ttl=3600)
async def is_local_model(model: str, api_url: str | None = None) -> bool:
    """Check if a model is available locally."""
    return model in await list_local_model_names(api_url)


async def async_ollama_call(
    prompt: str,
    *,
    model: OllamaModel = DEFAULT_OLLAMA_MODEL,
    memory: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    format: BaseModel | None = None,
    api_url: str | None = None,
) -> ChatResponse:
    client = _get_ollama_client(api_url)

    if not await is_local_model(model, api_url):
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
        kwargs["format"] = format.model_json_schema()
    response = await client.chat(**kwargs)
    return response
