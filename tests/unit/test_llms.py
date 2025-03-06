"""Smoke tests for LLM providers.

To defend against changes in API given the rapid rate of change in LLM providers.
"""

import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from tracecat.llm import (
    async_ollama_call,
    async_openai_call,
)
from tracecat.llm.ollama import ChatResponse
from tracecat.llm.openai import DEFAULT_OPENAI_MODEL, ChatCompletion
from tracecat.logger import logger

OLLAMA_URL = "http://localhost:11434"


def is_ollama_available() -> bool:
    """Check if Ollama is available by making a request to its version endpoint.

    Returns:
        bool: True if Ollama is available and responding, False otherwise
    """
    try:
        with httpx.Client() as client:
            response = client.get(f"{OLLAMA_URL}/api/version")
            return response.status_code == 200
    except httpx.RequestError:
        logger.warning("Ollama is not available")
        return False


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
                kwargs = {"api_url": OLLAMA_URL}
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
                    os.getenv("GITHUB_ACTIONS") is not None
                    or not is_ollama_available(),
                    reason="Skip Ollama tests in GitHub Actions CI or when Ollama is not available",
                ),
                pytest.mark.slow,
            ],
        ),
        pytest.param(
            ("openai", async_openai_call),
            marks=[
                pytest.mark.skipif(
                    os.getenv("OPENAI_API_KEY") is None,
                    reason="Skip OpenAI tests when API key is not available",
                ),
            ],
        ),
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

    match response:
        case ChatCompletion():
            assert "paris" in response.choices[0].message.content.lower()  # type: ignore
        case ChatResponse():
            assert "paris" in response.message.content.lower()  # type: ignore
        case _:
            pytest.fail(f"Unexpected response type: {type(response)}")


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


@pytest.mark.skipif(
    os.getenv("GITHUB_ACTIONS") is not None,
    reason="Skip memory tests in GitHub Actions CI. This is currently brokwn.",
)
@pytest.mark.anyio
async def test_memory(call_llm_params: tuple[str, Callable]):
    """Test conversation memory functionality."""

    memory = [
        {"role": "user", "content": "My favorite color is purple and I have 3 cats."},
        {
            "role": "assistant",
            "content": "Thanks for sharing! I'll remember that your favorite color is purple and you have 3 cats.",
        },
    ]
    prompt = "What is my favorite color and how many cats do I have? Please respond with only that information."
    provider, call_llm = call_llm_params
    kwargs = {
        "prompt": prompt,
        "memory": memory,
        **load_api_kwargs(provider),
    }
    response = await call_llm(**kwargs)

    # TODO: Create a simple router for content extraction
    # Corner case dealing with len(response.choices) > 0 for OpenAI
    match response:
        # OpenAI
        case ChatCompletion():
            response_content = response.choices[0].message.content
        # Ollama
        case ChatResponse():
            response_content = response.message.content
        case _:
            pytest.fail(f"Unexpected response type: {type(response)}")

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
