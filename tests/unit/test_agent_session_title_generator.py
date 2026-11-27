"""Tests for first-prompt session title generation."""

from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.session.title_generator import (
    TITLE_MAX_CHARS,
    TITLE_MAX_OUTPUT_TOKENS,
    TITLE_MAX_WORDS,
    fallback_session_title,
    generate_session_title,
    sanitize_session_title,
)


def test_sanitize_session_title_enforces_word_and_character_limits() -> None:
    too_many_words = "one two three four five six seven eight nine ten"
    assert sanitize_session_title(too_many_words) == " ".join(
        too_many_words.split()[:TITLE_MAX_WORDS]
    )

    too_many_chars = "a" * (TITLE_MAX_CHARS + 10)
    assert sanitize_session_title(too_many_chars) == "a" * TITLE_MAX_CHARS


def test_sanitize_session_title_handles_quotes_newlines_and_empty() -> None:
    assert sanitize_session_title('  "Investigate\nfailed logins"  ') == (
        "Investigate failed logins"
    )
    assert sanitize_session_title('""') is None
    assert sanitize_session_title("   \n\t  ") is None


@pytest.mark.anyio
async def test_generate_session_title_returns_sanitized_output() -> None:
    llm_complete = AsyncMock(return_value='  "Investigate API\nerrors in worker"  ')

    with patch("tracecat.agent.session.title_generator.llm_complete", llm_complete):
        title = await generate_session_title(
            user_prompt="The worker API keeps failing",
            session=AsyncMock(),
            role=AsyncMock(),
        )

    assert title == "Investigate API errors in worker"
    llm_complete.assert_awaited_once()


@pytest.mark.anyio
async def test_generate_session_title_raises_on_llm_error() -> None:
    with patch(
        "tracecat.agent.session.title_generator.llm_complete",
        side_effect=RuntimeError("bad model"),
    ):
        with pytest.raises(RuntimeError, match="bad model"):
            await generate_session_title(
                user_prompt="Find root cause",
                session=AsyncMock(),
                role=AsyncMock(),
            )


@pytest.mark.anyio
async def test_generate_session_title_skips_empty_prompt() -> None:
    llm_complete = AsyncMock()
    with patch("tracecat.agent.session.title_generator.llm_complete", llm_complete):
        assert (
            await generate_session_title(
                user_prompt="   ",
                session=AsyncMock(),
                role=AsyncMock(),
            )
            is None
        )
    llm_complete.assert_not_awaited()


@pytest.mark.anyio
async def test_generate_session_title_returns_none_for_empty_sanitized_output() -> None:
    with patch(
        "tracecat.agent.session.title_generator.llm_complete",
        AsyncMock(return_value='""'),
    ):
        assert (
            await generate_session_title(
                user_prompt="Find root cause",
                session=AsyncMock(),
                role=AsyncMock(),
            )
            is None
        )


@pytest.mark.anyio
async def test_generate_session_title_leaves_room_for_reasoning() -> None:
    llm_complete = AsyncMock(return_value="Investigate login failures")
    with patch("tracecat.agent.session.title_generator.llm_complete", llm_complete):
        await generate_session_title(
            user_prompt="Users cannot sign in",
            session=AsyncMock(),
            role=AsyncMock(),
        )

    assert llm_complete.await_args is not None
    kwargs = llm_complete.await_args.kwargs
    assert kwargs["max_tokens"] == TITLE_MAX_OUTPUT_TOKENS
    assert kwargs["reasoning_effort"] == "low"


def test_fallback_session_title_uses_first_non_empty_line() -> None:
    assert (
        fallback_session_title("\n\n  Why are logins failing?\nMore detail here")
        == "Why are logins failing?"
    )
    assert fallback_session_title("one two three four five six seven eight nine") == (
        "one two three four five six seven eight"
    )
    assert fallback_session_title(" \n\t ") is None
