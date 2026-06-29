"""Lightweight direct LLM calls against the org's default model.

Single-turn, no tools, no agent runtime. Used for best-effort background
tasks (e.g. auto-titling, AI suggestions) that need a cheap LLM call
without standing up a full agent session.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.common.config import TRACECAT__LITELLM_BASE_URL
from tracecat.agent.llm_routing import get_litellm_route_model
from tracecat.agent.service import AgentManagementService
from tracecat.agent.tokens import mint_llm_token
from tracecat.auth.types import Role
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)


class LLMCompletionError(RuntimeError):
    """Raised when a lightweight LLM completion request fails."""


async def complete(
    *,
    prompt: str,
    system_prompt: str,
    session: AsyncSession,
    role: Role,
    max_tokens: int | None = None,
    timeout_seconds: float = 8.0,
) -> str:
    """Single-turn LLM completion against the org's default model.

    Resolves the default model and credentials internally. Routes through
    LiteLLM (normal) or directly to the upstream (passthrough). Returns
    the raw text content.

    Raises:
        LLMCompletionError: If the provider request fails, returns an
            unexpected response, the model configuration is invalid, or the
            workspace lacks access to the model.
        TracecatNotFoundError: If the default model or credentials are missing.
    """
    svc = AgentManagementService(session, role)
    model_selection = await svc.get_default_model_selection()
    if model_selection is None:
        raise TracecatNotFoundError("No default model set")
    try:
        creds = await svc.get_catalog_credentials(model_selection.catalog_id)
    except TracecatAuthorizationError as e:
        raise LLMCompletionError(
            f"Workspace does not have access to the default model: {e}"
        ) from e
    if not creds:
        raise TracecatNotFoundError("No credentials for default model")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    passthrough = creds.get("CUSTOM_MODEL_PROVIDER_PASSTHROUGH", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    try:
        async with asyncio.timeout(timeout_seconds):
            if passthrough:
                resp = await _call_passthrough(
                    messages=messages,
                    model_name=creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
                    or model_selection.model_name,
                    base_url=creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"),
                    api_key=creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"),
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
            else:
                if role.workspace_id is None:
                    raise TracecatAuthorizationError("Role has no workspace_id")
                if role.organization_id is None:
                    raise TracecatAuthorizationError("Role has no organization_id")
                token = mint_llm_token(
                    workspace_id=role.workspace_id,
                    organization_id=role.organization_id,
                    session_id=uuid.uuid4(),
                    model=get_litellm_route_model(
                        model_name=model_selection.model_name,
                        model_provider=model_selection.model_provider,
                        passthrough=False,
                    ),
                    provider=model_selection.model_provider,
                    catalog_id=model_selection.catalog_id,
                )
                resp = await _call_litellm(
                    messages=messages,
                    token=token,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
    except TimeoutError as e:
        raise LLMCompletionError("Timed out while waiting for LLM completion") from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:500].strip()
        message = f"LLM provider returned HTTP {e.response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise LLMCompletionError(message) from e
    except httpx.RequestError as e:
        raise LLMCompletionError(f"LLM request failed: {e}") from e
    except TracecatValidationError as e:
        detail = str(e) or "model configuration failed validation"
        raise LLMCompletionError(f"LLM model configuration is invalid: {detail}") from e

    return resp


async def _call_litellm(
    *,
    messages: list[dict[str, str]],
    token: str,
    max_tokens: int | None,
    timeout_seconds: float,
) -> str:
    body: dict[str, Any] = {"messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            f"{TRACECAT__LITELLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        resp.raise_for_status()
    return _extract_chat_completion_content(resp)


async def _call_passthrough(
    *,
    messages: list[dict[str, str]],
    model_name: str,
    base_url: str | None,
    api_key: str | None,
    max_tokens: int | None,
    timeout_seconds: float,
) -> str:
    if not base_url:
        raise TracecatValidationError("Custom model passthrough base URL is required")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
    return _extract_chat_completion_content(resp)


def _extract_chat_completion_content(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMCompletionError("LLM completion response was malformed") from e
    if not isinstance(content, str):
        raise LLMCompletionError("LLM completion response content was not text")
    return content
