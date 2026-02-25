"""Generate concise session titles from the first user prompt."""

import asyncio

from pydantic_ai import Agent, UsageLimits

from tracecat.agent.providers import get_model

TITLE_MAX_WORDS = 8
TITLE_MAX_CHARS = 60
TITLE_GEN_TIMEOUT_SECONDS = 8.0
TITLE_PROMPT_MAX_CHARS = 2000

_TITLE_INSTRUCTIONS = (
    "Generate a concise chat title from the user's first message.\n"
    "Rules:\n"
    "- Return plain text only.\n"
    "- No quotes, markdown, or trailing punctuation.\n"
    "- Maximum 8 words and 60 characters.\n"
    "- Capture the user's intent clearly."
)


def sanitize_session_title(raw_title: str) -> str | None:
    """Sanitize and constrain model output for session title use."""
    normalized = " ".join(raw_title.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        return None

    quote_chars = "\"'`“”‘’"
    # Strip paired surrounding quotes repeatedly (e.g., '\"title\"').
    while (
        len(normalized) >= 2
        and normalized[0] in quote_chars
        and normalized[-1] in quote_chars
    ):
        normalized = normalized[1:-1].strip()
        if not normalized:
            return None

    words = normalized.split()
    if len(words) > TITLE_MAX_WORDS:
        normalized = " ".join(words[:TITLE_MAX_WORDS])

    if len(normalized) > TITLE_MAX_CHARS:
        normalized = normalized[:TITLE_MAX_CHARS].rstrip()

    return normalized or None


def _build_title_prompt(user_prompt: str) -> str:
    clipped = user_prompt[:TITLE_PROMPT_MAX_CHARS]
    return f"User message:\n{clipped}"


async def generate_session_title(
    *,
    user_prompt: str,
    model_name: str,
    model_provider: str,
    timeout_seconds: float = TITLE_GEN_TIMEOUT_SECONDS,
) -> str | None:
    """Best-effort title generation via direct PydanticAI call."""
    prompt = user_prompt.strip()
    if not prompt:
        return None

    try:
        model = get_model(model_name, model_provider)
        agent = Agent(
            model=model,
            instructions=_TITLE_INSTRUCTIONS,
            output_type=str,
            retries=0,
            instrument=False,
        )
        usage_limits = UsageLimits(request_limit=1, tool_calls_limit=0)
        async with asyncio.timeout(timeout_seconds):
            result = await agent.run(
                _build_title_prompt(prompt),
                usage_limits=usage_limits,
            )
    except Exception:
        return None

    return sanitize_session_title(result.output)
