"""Tests for first-prompt session title generation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.session.title_generator import (
    TITLE_MAX_CHARS,
    TITLE_MAX_WORDS,
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
    mock_agent = AsyncMock()
    mock_agent.run.return_value = SimpleNamespace(
        output='  "Investigate API\nerrors in worker"  '
    )

    with (
        patch(
            "tracecat.agent.session.title_generator.get_model", return_value=object()
        ),
        patch("tracecat.agent.session.title_generator.Agent", return_value=mock_agent),
    ):
        title = await generate_session_title(
            user_prompt="The worker API keeps failing",
            model_name="gpt-4o-mini",
            model_provider="openai",
        )

    assert title == "Investigate API errors in worker"
    mock_agent.run.assert_awaited_once()


@pytest.mark.anyio
async def test_generate_session_title_returns_none_on_model_error() -> None:
    with patch(
        "tracecat.agent.session.title_generator.get_model",
        side_effect=RuntimeError("bad model"),
    ):
        title = await generate_session_title(
            user_prompt="Find root cause",
            model_name="bad-model",
            model_provider="openai",
        )
    assert title is None


@pytest.mark.anyio
async def test_generate_session_title_returns_none_on_timeout() -> None:
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(side_effect=TimeoutError())

    with (
        patch(
            "tracecat.agent.session.title_generator.get_model", return_value=object()
        ),
        patch("tracecat.agent.session.title_generator.Agent", return_value=mock_agent),
    ):
        title = await generate_session_title(
            user_prompt="Find root cause",
            model_name="gpt-4o-mini",
            model_provider="openai",
        )
    assert title is None
