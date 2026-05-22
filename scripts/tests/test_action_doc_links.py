from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
import pytest

from tracecat.registry.repository import Repository

DOC_LINK_CHECK_ATTEMPTS = 3
DOC_LINK_CHECK_CONCURRENCY = 8
DOC_LINK_RETRY_DELAY_SECONDS = 0.5


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


async def _check_url(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    *,
    max_attempts: int = DOC_LINK_CHECK_ATTEMPTS,
    retry_delay: float = DOC_LINK_RETRY_DELAY_SECONDS,
) -> tuple[int | None, str | None]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    async with semaphore:
        last_error: httpx.HTTPError | None = None
        for attempt in range(max_attempts):
            try:
                response = await client.get(url, follow_redirects=True)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                return None, f"{type(exc).__name__}: {exc}"
            return response.status_code, None

        assert last_error is not None
        return None, (
            f"{type(last_error).__name__} after {max_attempts} attempts: {last_error}"
        )


def test_check_url_retries_transient_http_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(204)

    async def run() -> tuple[int | None, str | None]:
        semaphore = asyncio.Semaphore(1)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await _check_url(
                client=client,
                semaphore=semaphore,
                url="https://example.com/docs",
                max_attempts=2,
                retry_delay=0,
            )

    assert asyncio.run(run()) == (204, None)
    assert attempts == 2


def test_check_url_reports_last_transient_error_after_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection failed", request=request)

    async def run() -> tuple[int | None, str | None]:
        semaphore = asyncio.Semaphore(1)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await _check_url(
                client=client,
                semaphore=semaphore,
                url="https://example.com/docs",
                max_attempts=2,
                retry_delay=0,
            )

    assert asyncio.run(run()) == (
        None,
        "ConnectError after 2 attempts: connection failed",
    )
    assert attempts == 2


@pytest.mark.skipif(
    os.environ.get("TRACECAT_TEST_ACTION_DOC_LINKS") != "1",
    reason="live action documentation link check is only enabled in CI",
)
def test_action_doc_urls_are_reachable() -> None:
    url_actions = _action_doc_urls()
    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = {"User-Agent": "Tracecat-CI-Link-Checker/1.0"}

    async def check_all() -> dict[str, str]:
        semaphore = asyncio.Semaphore(DOC_LINK_CHECK_CONCURRENCY)
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
            elif status_code is None or (
                status_code >= 400 and status_code not in {401, 403, 429}
            ):
                broken[url] = f"HTTP {status_code}"
        return broken

    broken = asyncio.run(check_all())

    assert not broken, "\n".join(
        f"{url}: {reason}; actions={', '.join(url_actions[url][:5])}"
        + (f" (+{len(url_actions[url]) - 5} more)" if len(url_actions[url]) > 5 else "")
        for url, reason in sorted(broken.items())
    )
