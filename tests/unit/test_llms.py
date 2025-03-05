"""Smoke tests for LLM providers.

To defend against changes in API given the rapid rate of change in LLM providers.
"""

import os
from collections.abc import Callable
from enum import Enum
from typing import Any

import pytest
from pydantic import BaseModel, Field

from tracecat.llm import (
    async_ollama_call,
    async_openai_call,
)
from tracecat.llm.ollama import ChatResponse
from tracecat.llm.openai import DEFAULT_OPENAI_MODEL, ParsedChatCompletion


class ExpectedChatResponseType(Enum):
    OPENAI = ParsedChatCompletion
    OLLAMA = ChatResponse


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


@pytest.fixture(
    scope="session",
    params=[
        pytest.param(
            ("ollama", async_ollama_call),
            marks=[
                pytest.mark.skipif(
                    os.getenv("GITHUB_ACTIONS") is not None,
                    reason="Skip Ollama tests in GitHub Actions CI",
                ),
                pytest.mark.slow,
            ],
        ),
        pytest.param(("openai", async_openai_call)),
    ],
    ids=["ollama", "openai"],
)
def call_llm_params(request: pytest.FixtureRequest) -> tuple[str, Callable]:
    return request.param


@pytest.mark.anyio
async def test_user_prompt(call_llm_params: tuple[str, Callable]):
    provider, call_llm = call_llm_params
    prompt = "What is the capital of France?"
    kwargs = {"prompt": prompt}
    kwargs = {
        **kwargs,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)
    assert response is not None


@pytest.mark.anyio
async def test_system_prompt(call_llm_params: tuple[str, Callable]):
    """Test system prompt functionality."""
    prompt = "What is an LLM?"
    system_prompt = (
        "You are a helpful AI assistant that explains technical concepts clearly."
    )
    provider, call_llm = call_llm_params
    kwargs = {
        "prompt": prompt,
        "system_prompt": system_prompt,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)
    assert response is not None


class BooleanResponse(BaseModel):
    """Response to a boolean question."""

    answer: bool = Field(strict=True)


@pytest.mark.anyio
async def test_memory(call_llm_params: tuple[str, Callable]):
    """Test conversation memory functionality."""

    memory = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I help you today?"},
    ]
    prompt = (
        "What was my first message (as the user) to you (the assistant)? "
        'Hint: Was my first message to you "Hello"?'
    )
    provider, call_llm = call_llm_params
    kwargs = {
        "prompt": prompt,
        "memory": memory,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)

    # TODO: Create a simple router for content extraction
    # Corner case dealing with len(response.choices) > 0 for OpenAI
    expected_response_type = ExpectedChatResponseType[provider.upper()]
    match expected_response_type:
        case ExpectedChatResponseType.OPENAI:
            response_content = response.choices[0].message.content
        case ExpectedChatResponseType.OLLAMA:
            response_content = response.message.content

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
        **load_api_kwargs("openai"),
    }
    judge_response = await async_openai_call(**verification_kwargs)
    judge_message = judge_response.choices[0].message

    if judge_message.refusal:
        pytest.fail("LLM judge refused to verify the response")

    judge_message = judge_response.choices[0].message.parsed  # type: ignore
    assert judge_message.answer, (
        f"LLM judge determined response was incorrect.\n"
        f"Assistant response: {response_content}\n"
        f"Judge response: {judge_message.answer}"
    )
