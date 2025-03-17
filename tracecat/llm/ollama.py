"""Ollama LLM client.

Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from enum import StrEnum
from typing import Any

import ollama
from async_lru import alru_cache
from ollama import ChatResponse
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

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
    GEMMA3_1B = "gemma3:1b"
    GEMMA3_4B = "gemma3:4b"
    GEMMA3_12B = "gemma3:12b"
    # Smol instruction tuned
    GEMMA3_1B_INSTRUCT = "gemma3:1b-it-q8_0"
    GEMMA3_4B_INSTRUCT = "gemma3:4b-it-q8_0"
    GEMMA3_12B_INSTRUCT = "gemma3:12b-it-q8_0"
    # Large models
    LLAMA33 = "llama3.3:70b"
    MISTRAL_LARGE = "mistral-large:123b"
    MIXTRAL = "mixtral:8x7b"
    MIXTRAL_22B = "mixtral:8x22b"
    GEMMA3_27B = "gemma3:27b"
    # Instruction tuned models
    GEMMA3_27B_INSTRUCT = "gemma3:27b-it-q8_0"


DEFAULT_OLLAMA_MODEL = OllamaModel.GEMMA3_1B
DEFAULT_OLLAMA_INSTRUCT_MODEL = OllamaModel.GEMMA3_1B_INSTRUCT


def _get_ollama_client(api_url: str | None = None) -> ollama.AsyncClient:
    return ollama.AsyncClient(host=api_url or config.OLLAMA__API_URL)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
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
    model: OllamaModel | None = None,
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
        model = model or DEFAULT_OLLAMA_INSTRUCT_MODEL
        kwargs["format"] = format.model_json_schema()
    else:
        model = model or DEFAULT_OLLAMA_MODEL

    response = await client.chat(**kwargs)
    return response
