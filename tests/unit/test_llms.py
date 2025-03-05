"""Smoke tests for LLM providers.

To defend against changes in API given the rapid rate of change in LLM providers.
"""

import os
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from tracecat.llm import (
    OllamaModel,
    OpenAIModel,
    async_ollama_call,
    async_openai_call,
)
from tracecat.llm.openai import DEFAULT_OPENAI_MODEL


def load_api_kwargs(provider: str) -> dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv()
    try:
        match provider:
            case "openai":
                api_key = os.environ["OPENAI_API_KEY"]
                kwargs = {"api_key": api_key}
            case "ollama":
                # Requires docker-compose.dev.yml stack
                # with ollama service exposed on port 11434
                kwargs = {"api_url": "http://localhost:11434"}
            case _:
                return {}
    except KeyError:
        pytest.fail(f"API key for LLM provider {provider!r} not found")
    return kwargs


@pytest.mark.parametrize(
    "call_llm,prompt_kwarg",
    [
        (async_ollama_call, "prompt"),
        (async_openai_call, "prompt"),
    ],
    ids=["ollama", "openai"],
)
@pytest.mark.anyio
async def test_user_prompt(call_llm: Callable, prompt_kwarg: str):
    prompt = "What is the capital of France?"
    kwargs = {prompt_kwarg: prompt}
    response = await call_llm(**kwargs)
    assert response is not None


@pytest.mark.parametrize(
    "provider,call_llm,model",
    [
        ("ollama", async_ollama_call, OllamaModel.LLAMA32),
        ("openai", async_openai_call, OpenAIModel.GPT4O),
    ],
)
@pytest.mark.anyio
async def test_system_prompt(provider: str, call_llm: Callable, model: str):
    """Test system prompt functionality."""
    prompt = "What is an LLM?"
    system_prompt = (
        "You are a helpful AI assistant that explains technical concepts clearly."
    )
    kwargs = {
        "prompt": prompt,
        "system_prompt": system_prompt,
        "model": model,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)
    assert response is not None


class BooleanResponse(BaseModel):
    """Response to a boolean question."""

    answer: bool


@pytest.mark.parametrize(
    "provider,call_llm,model",
    [
        ("ollama", async_ollama_call, OllamaModel.LLAMA32),
        ("openai", async_openai_call, OpenAIModel.GPT4O),
    ],
)
@pytest.mark.anyio
async def test_memory(provider: str, call_llm: Callable, model: str):
    """Test conversation memory functionality."""

    memory = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I help you today?"},
    ]
    prompt = "What was my first message to you?"
    kwargs = {
        "prompt": prompt,
        "memory": memory,
        "model": model,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)
    response_content = response.choices[0].message.content

    # TODO: Replace with LLM-as-a-judge core action
    # Use default OpenAI model as a judge to verify if response is accurate
    judge_prompt = f"""
    Based on the following conversation, determine if the final response from the LLM assistant is accurate:

    <Conversation>
    User: {memory[0]["content"]}
    Assistant: {memory[1]["content"]}
    User: {prompt}
    </Conversation>

    <FinalResponse>
    {response_content}
    </FinalResponse>
    """

    verification_kwargs = {
        "prompt": judge_prompt,
        "model": DEFAULT_OPENAI_MODEL,
        "response_format": BooleanResponse,
        **load_api_kwargs(provider),
    }
    judge_response = await async_openai_call(**verification_kwargs)
    judge_message = judge_response.choices[0].message

    if judge_message.refusal:
        pytest.fail("LLM judge refused to verify the response")

    judge_message = judge_response.choices[0].message.parsed  # type: ignore
    assert judge_message.answer is True
