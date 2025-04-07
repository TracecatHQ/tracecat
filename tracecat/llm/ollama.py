"""Ollama LLM client.

Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from enum import StrEnum
from typing import Any

import ollama
from async_lru import alru_cache
from ollama import ChatResponse
from pydantic import BaseModel

from tracecat.logger import logger


class OllamaModel(StrEnum):
    # Smol models (<15GB)
    GEMMA3_12B = "gemma3:12b"
    GEMMA3_1B = "gemma3:1b"
    GEMMA3_4B = "gemma3:4b"
    LLAMA32 = "llama3.2:3b"
    LLAMA32_1B = "llama3.2:1b"
    MISTRAL_SMALL = "mistral-small:24b"
    # Smol instruction tuned
    GEMMA3_12B_INSTRUCT = "gemma3:12b-it-q8_0"
    GEMMA3_1B_INSTRUCT = "gemma3:1b-it-q8_0"
    GEMMA3_4B_INSTRUCT = "gemma3:4b-it-q8_0"
    LLAMA32_1B_INSTRUCT = "llama3.2:1b-instruct-q8_0"
    LLAMA32_3B_INSTRUCT = "llama3.2:3b-instruct-q8_0"
    # Large models
    GEMMA3_27B = "gemma3:27b"
    LLAMA33 = "llama3.3:70b"
    MISTRAL_LARGE = "mistral-large:123b"
    MIXTRAL = "mixtral:8x7b"
    MIXTRAL_22B = "mixtral:8x22b"
    # Instruction tuned models
    GEMMA3_27B_INSTRUCT = "gemma3:27b-it-q8_0"
    LLAMA33_INSTRUCT = "llama3.3:70b-instruct-q8_0"
    MISTRAL_SMALL_INSTRUCT = "mistral-small:24b-instruct-2501-q8_0"


DEFAULT_OLLAMA_MODEL = OllamaModel.GEMMA3_1B
DEFAULT_OLLAMA_INSTRUCT_MODEL = OllamaModel.GEMMA3_1B_INSTRUCT


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
async def is_local_model(model: OllamaModel, base_url: str) -> bool:
    """Check if a model is available locally."""
    model_name = model.value
    return model_name in await list_local_model_names(base_url)


async def async_ollama_call(
    prompt: str,
    *,
    model: OllamaModel = DEFAULT_OLLAMA_MODEL,
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
    else:
        model = model or DEFAULT_OLLAMA_MODEL

    response = await client.chat(**kwargs)
    return response
