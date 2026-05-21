from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import anyio
import pytest

from tests.smoke.agent.smoke_client import (
    AgentSmokeClient,
    ProviderSpec,
    SmokeEnvironment,
    cleanup_local_agent_smoke_fixtures,
    primary_provider_name,
)


@pytest.fixture(scope="session", autouse=True)
def smoke_fixture_cleanup() -> Iterator[None]:
    yield
    cleanup_local_agent_smoke_fixtures()


@pytest.fixture(autouse=True)
async def smoke_test_timeout() -> AsyncIterator[None]:
    timeout_seconds = SmokeEnvironment.from_env().test_timeout_seconds
    try:
        with anyio.fail_after(timeout_seconds):
            yield
    except TimeoutError as exc:
        raise AssertionError(f"Agent smoke test exceeded {timeout_seconds}s") from exc


@pytest.fixture
async def smoke_client() -> AsyncIterator[AgentSmokeClient]:
    async with AgentSmokeClient(SmokeEnvironment.from_env()) as client:
        yield client


@pytest.fixture
async def primary_provider(smoke_client: AgentSmokeClient) -> ProviderSpec:
    provider = await smoke_client.ensure_provider(primary_provider_name())
    await smoke_client.ensure_default_model(provider)
    return provider
