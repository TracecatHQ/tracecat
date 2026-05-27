from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
import pytest

from tracecat.registry.repository import Repository

_ALLOWED_UNREACHABLE_STATUS_CODES = {401, 403, 429}
_MAX_LINK_CHECK_ATTEMPTS = 3
_TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _action_doc_urls() -> dict[str, tuple[str, ...]]:
    repo = Repository()
    repo.init(include_base=True, include_templates=True)

    actions_by_url: dict[str, list[str]] = defaultdict(list)
    for action_name, action in repo:
        if action.doc_url:
            actions_by_url[action.doc_url].append(action_name)

    return {url: tuple(actions) for url, actions in actions_by_url.items()}


def _format_failures(failures: Mapping[str, tuple[str, ...]]) -> str:
    return "\n".join(
        f"{url}: {', '.join(actions[:5])}"
        + (f" (+{len(actions) - 5} more)" if len(actions) > 5 else "")
        for url, actions in sorted(failures.items())
    )


def test_action_doc_urls_are_static_http_links() -> None:
    malformed: dict[str, tuple[str, ...]] = {}
    for url, actions in _action_doc_urls().items():
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or "<" in url
            or ">" in url
            or "${{" in url
            or " " in url
        ):
            malformed[url] = actions

    assert not malformed, _format_failures(malformed)


def test_ai_actions_link_to_tracecat_docs() -> None:
    repo = Repository()
    repo.init(include_base=True, include_templates=False)

    assert repo.get("ai.agent").doc_url == "https://docs.tracecat.com/agents/ai-agent"
    assert repo.get("ai.action").doc_url == "https://docs.tracecat.com/agents/ai-action"


async def _request_url(client: httpx.AsyncClient, url: str) -> int:
    response = await client.head(url, follow_redirects=True)
    if response.status_code in {405, 501}:
        response = await client.get(url, follow_redirects=True)
    return response.status_code


def _is_transient_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in _TRANSIENT_STATUS_CODES


async def _check_url(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
) -> tuple[int | None, str | None]:
    async with semaphore:
        last_error: str | None = None
        last_status_code: int | None = None
        for attempt in range(1, _MAX_LINK_CHECK_ATTEMPTS + 1):
            try:
                status_code = await _request_url(client, url)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status_code = None
            except httpx.HTTPError as exc:
                return None, f"{type(exc).__name__}: {exc}"
            else:
                last_status_code = status_code
                last_error = None
                if not _is_transient_status(status_code):
                    return status_code, None

            if attempt < _MAX_LINK_CHECK_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)

        if last_error:
            return None, f"{last_error} after {_MAX_LINK_CHECK_ATTEMPTS} attempts"
        return last_status_code, None


def _is_broken_link_status(status_code: int | None) -> bool:
    return status_code is None or (
        status_code >= 400 and status_code not in _ALLOWED_UNREACHABLE_STATUS_CODES
    )


@pytest.mark.skipif(
    os.environ.get("TRACECAT_TEST_ACTION_DOC_LINKS") != "1",
    reason="live action documentation link check is only enabled in CI",
)
def test_action_doc_urls_are_reachable() -> None:
    url_actions = _action_doc_urls()
    timeout = httpx.Timeout(12.0, connect=5.0)
    headers = {"User-Agent": "Tracecat-CI-Link-Checker/1.0"}

    async def check_all() -> dict[str, str]:
        semaphore = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            results = await asyncio.gather(
                *(
                    _check_url(client=client, semaphore=semaphore, url=url)
                    for url in url_actions
                )
            )

        broken: dict[str, str] = {}
        for url, (status_code, error) in zip(url_actions, results, strict=True):
            if error:
                broken[url] = error
            elif _is_broken_link_status(status_code):
                broken[url] = f"HTTP {status_code}"
        return broken

    broken = asyncio.run(check_all())

    assert not broken, "\n".join(
        f"{url}: {reason}; actions={', '.join(url_actions[url][:5])}"
        + (f" (+{len(url_actions[url]) - 5} more)" if len(url_actions[url]) > 5 else "")
        for url, reason in sorted(broken.items())
    )
