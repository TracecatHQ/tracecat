"""Smoke tests for LLM providers.

To defend against changes in API given the rapid rate of change in LLM providers.
"""

import os
from collections.abc import Callable
from typing import Any

import orjson
import pytest
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.responses import Response

from tracecat.llm import async_openai_call, async_openai_chat_completion

pytestmark = pytest.mark.llm


def load_api_kwargs(provider: str) -> dict[str, Any]:
    match provider:
        case "openai":
            api_key = os.environ["OPENAI_API_KEY"]
            kwargs = {"api_key": api_key}
        case _:
            pytest.fail(f"API key for LLM provider {provider!r} not found")
    return kwargs


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            ("openai", async_openai_call),
            marks=[
                pytest.mark.skipif(
                    os.getenv("OPENAI_API_KEY") is None,
                    reason="Skip OpenAI tests when API key is not available",
                ),
                pytest.mark.llm,
            ],
        ),
        pytest.param(
            ("openai", async_openai_chat_completion),
            marks=[
                pytest.mark.skipif(
                    os.getenv("OPENAI_API_KEY") is None,
                    reason="Skip OpenAI tests when API key is not available",
                ),
                pytest.mark.llm,
            ],
        ),
    ],
    ids=["openai_responses", "openai_chat_completion"],
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
        case Response():
            assert "paris" in response.output_text.lower()
        case ChatCompletion():
            assert "paris" in response.choices[0].message.content.lower()  # type: ignore
        case _:
            pytest.fail(f"Unexpected response type: {type(response)}")


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
        case Response():
            response_content = response.output_text
        case ChatCompletion():
            response_content = response.choices[0].message.content  # type: ignore
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
        "model": "gpt-4o",
        "text_format": {
            "type": "json_schema",
            "name": "is_answer_correct",
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "boolean",
                        "description": "Whether the answer is correct",
                    },
                },
                "required": ["answer"],
                "strict": True,
                "additionalProperties": False,
            },
            "strict": True,
        },
        **load_api_kwargs("openai"),
    }
    judge_response = await async_openai_call(**verification_kwargs)

    if judge_response.incomplete_details:
        pytest.fail(
            f"LLM judge was unable to verify the response: {judge_response.incomplete_details}"
        )

    judge_answer = orjson.loads(judge_response.output_text)["answer"]
    assert isinstance(judge_answer, bool)
    assert judge_answer, (
        f"LLM judge determined response was incorrect.\n"
        f"Assistant response: {response_content}\n"
        f"Judge answer: {judge_answer}"
    )
