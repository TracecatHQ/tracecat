"""Generate concise session titles from the first user prompt."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.llm import complete as llm_complete
from tracecat.auth.types import Role

TITLE_MAX_WORDS = 8
TITLE_MAX_CHARS = 60
TITLE_GEN_TIMEOUT_SECONDS = 15.0
TITLE_PROMPT_MAX_CHARS = 2000

_SYSTEM_PROMPT = (
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
    # Strip paired surrounding quotes repeatedly (e.g., '"title"').
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


async def generate_session_title(
    *,
    user_prompt: str,
    session: AsyncSession,
    role: Role,
    timeout_seconds: float = TITLE_GEN_TIMEOUT_SECONDS,
) -> str | None:
    prompt = user_prompt.strip()
    if not prompt:
        return None
    raw = await llm_complete(
        prompt=f"User message:\n{prompt[:TITLE_PROMPT_MAX_CHARS]}",
        system_prompt=_SYSTEM_PROMPT,
        session=session,
        role=role,
        max_tokens=30,
        timeout_seconds=timeout_seconds,
    )
    return sanitize_session_title(raw) if raw else None
