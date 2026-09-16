"""Automatic provider selection against PostgreSQL and a local HTTP provider."""

import asyncio
import hashlib
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_engine, reset_async_engine
from tracecat.db.models import (
    AgentCatalog,
    AgentModelAccess,
    Organization,
    OrganizationSecret,
    OrganizationSetting,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.service import (
    WorkspaceEmbeddingService,
    embed_current,
    resolve_embedding_configuration,
)
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.types import (
    EmbeddingInput,
    EmbeddingRequest,
    SearchScope,
    SearchState,
)
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.schemas import SecretKeyValue


@dataclass
class ProviderServer:
    status: int = 200
    calls: list[str] = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    hold: bool = False


@pytest.fixture
def provider_server():
    state = ProviderServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = orjson.loads(self.rfile.read(int(self.headers["content-length"])))
            state.calls.append(self.path)
            state.entered.set()
            if state.hold:
                state.release.wait(10)
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if "batchEmbedContents" in self.path:
                payload = {
                    "embeddings": [{"values": [1.0] * 3072} for _ in data["requests"]]
                }
            elif "invoke" in self.path:
                payload = {"embedding": [1.0] * 1024, "inputTextTokenCount": 8}
            else:
                payload = {
                    "model": data["model"],
                    "data": [
                        {"index": i, "embedding": [1.0] * 1536}
                        for i in reversed(range(len(data["input"])))
                    ],
                    "usage": {"prompt_tokens": 8, "total_tokens": 8},
                }
            self.wfile.write(orjson.dumps(payload))

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, server.server_port
    state.release.set()
    server.shutdown()
    server.server_close()
    thread.join()


class LocalProviderTransport(httpx.AsyncHTTPTransport):
    def __init__(self, port: int):
        super().__init__()
        self.port = port

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host in {
            "api.openai.com",
            "generativelanguage.googleapis.com",
            "bedrock-runtime.us-east-1.amazonaws.com",
            "bedrock-runtime.us-west-2.amazonaws.com",
        }
        request.url = request.url.copy_with(
            scheme="http", host="127.0.0.1", port=self.port
        )
        return await super().handle_async_request(request)


def encrypted(values: dict[str, str]) -> bytes:
    return encrypt_keyvalues(
        [SecretKeyValue(key=k, value=SecretStr(v)) for k, v in values.items()],
        key=get_db_encryption_key(),
    )


@dataclass
class ConfigurationCase:
    roles: tuple[Role, ...]
    client: EmbeddingClient
    server: ProviderServer
    sessions: async_sessionmaker[AsyncSession]

    def scope(self, index=0):
        role = self.roles[index]
        assert role.organization_id is not None and role.workspace_id is not None
        return SearchScope(role.organization_id, role.workspace_id)

    def service(self, index=0):
        return WorkspaceEmbeddingService(self.roles[index])

    async def request(self, index=0):
        scope = self.scope(index)
        selected = await resolve_embedding_configuration(scope)
        assert selected is not None
        text = "synthetic passage"
        return EmbeddingRequest(
            scope,
            selected.version,
            selected.spec.dimensions,
            (EmbeddingInput(0, hashlib.sha256(text.encode()).hexdigest(), text),),
        )

    async def connect(self, provider="openai", index=0, values=None):
        scope = self.scope(index)
        keys = (
            values
            or {
                "openai": {"OPENAI_API_KEY": "synthetic-openai"},
                "gemini": {"GEMINI_API_KEY": "synthetic-gemini"},
                "bedrock": {
                    "AWS_BEARER_TOKEN_BEDROCK": "synthetic-bedrock",
                    "AWS_REGION": "us-east-1",
                },
                "anthropic": {"ANTHROPIC_API_KEY": "synthetic-anthropic"},
            }[provider]
        )
        async with self.sessions.begin() as session:
            catalog = AgentCatalog(
                organization_id=scope.organization_id,
                model_provider=provider,
                model_name=f"synthetic-{provider}-chat",
            )
            session.add(catalog)
            await session.flush()
            session.add(
                AgentModelAccess(
                    organization_id=scope.organization_id, catalog_id=catalog.id
                )
            )
            secret = OrganizationSecret(
                organization_id=scope.organization_id,
                name=f"agent-{provider}-credentials",
                environment="default",
                encrypted_keys=encrypted(keys),
            )
            session.add(secret)
            await session.flush()
            return catalog.id, secret.id

    async def prefer(self, catalog_id, index=0):
        async with self.sessions.begin() as session:
            await session.execute(
                delete(OrganizationSetting).where(
                    OrganizationSetting.organization_id
                    == self.scope(index).organization_id,
                    OrganizationSetting.key == "agent_default_model_catalog_id",
                )
            )
            session.add(
                OrganizationSetting(
                    organization_id=self.scope(index).organization_id,
                    key="agent_default_model_catalog_id",
                    value=orjson.dumps(str(catalog_id)),
                    value_type="json",
                )
            )


@pytest.fixture
async def embedding_case(
    monkeypatch, provider_server
) -> AsyncIterator[ConfigurationCase]:
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url)
    await get_async_engine().dispose()
    reset_async_engine()
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    roles = tuple(
        Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            scopes=frozenset({"workspace:read"}),
        )
        for _ in range(2)
    )
    async with sessions.begin() as session:
        for role in roles:
            session.add(
                Organization(
                    id=role.organization_id,
                    name="Synthetic organization",
                    slug=str(role.organization_id),
                )
            )
        await session.flush()
        for role in roles:
            session.add(
                Workspace(
                    id=role.workspace_id,
                    organization_id=role.organization_id,
                    name="Synthetic workspace",
                )
            )
    state, port = provider_server
    async with httpx.AsyncClient(transport=LocalProviderTransport(port)) as http:
        yield ConfigurationCase(roles, EmbeddingClient(http), state, sessions)
    async with sessions.begin() as session:
        for role in roles:
            for model in (
                SearchEmbeddingConfig,
                SearchWorkspaceState,
                AgentModelAccess,
                AgentCatalog,
                OrganizationSecret,
                OrganizationSetting,
                Workspace,
            ):
                await session.execute(
                    delete(model).where(model.organization_id == role.organization_id)
                )
            await session.execute(
                delete(Organization).where(Organization.id == role.organization_id)
            )
    await engine.dispose()
    await get_async_engine().dispose()
    reset_async_engine()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,dimensions", [("openai", 1536), ("gemini", 3072), ("bedrock", 1024)]
)
async def test_automatically_reuses_existing_provider(
    embedding_case, provider, dimensions
):
    case = embedding_case
    await case.connect(provider)
    status = await case.service().get()
    assert status.available and status.configuration.provider == provider
    assert status.configuration.dimensions == dimensions
    assert not case.server.calls
    assert "credential" not in status.model_dump_json()
    assert "synthetic-" not in status.model_dump_json()
    result = await embed_current(await case.request(), case.client)
    assert len(result.results[0].vector) == dimensions
    assert len(case.server.calls) == 1


@pytest.mark.anyio
async def test_no_supported_provider_is_unavailable_without_network_or_state(
    embedding_case,
):
    case = embedding_case
    await case.connect("anthropic")
    assert not (await case.service().get()).available
    async with case.sessions() as session:
        assert (
            await session.scalar(
                select(SearchWorkspaceState).where(
                    SearchWorkspaceState.workspace_id == case.scope().workspace_id
                )
            )
            is None
        )
    assert await resolve_embedding_configuration(case.scope()) is None
    request = EmbeddingRequest(case.scope(), 0, 1536, ())
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == EmbeddingErrorCode.NOT_CONFIGURED
    assert not case.server.calls


@pytest.mark.anyio
async def test_default_provider_and_fixed_fallback_order(embedding_case):
    case = embedding_case
    gemini, _ = await case.connect("gemini")
    await case.connect("openai")
    first = await resolve_embedding_configuration(case.scope())
    assert first is not None
    assert first.spec.provider == "openai"
    await case.prefer(gemini)
    second = await resolve_embedding_configuration(case.scope())
    assert second is not None
    assert second.spec.provider == "gemini" and second.version > first.version
    assert (await case.service().get()).reindex_required
    async with case.sessions() as session:
        state = await session.get(
            SearchWorkspaceState,
            (case.scope().organization_id, case.scope().workspace_id),
        )
        assert state.reconciliation_required


@pytest.mark.anyio
async def test_workspace_access_overrides_and_cross_org_isolation(embedding_case):
    case = embedding_case
    await case.connect("openai")
    gemini, _ = await case.connect("gemini")
    assert not (await case.service(1).get()).available
    async with case.sessions.begin() as session:
        session.add(
            AgentModelAccess(
                organization_id=case.scope().organization_id,
                workspace_id=case.scope().workspace_id,
                catalog_id=gemini,
            )
        )
    assert (await case.service().get()).configuration.provider == "gemini"
    # A grant cannot make another organization's catalog/provider usable.
    async with case.sessions.begin() as session:
        session.add(
            AgentModelAccess(
                organization_id=case.scope(1).organization_id,
                workspace_id=case.scope(1).workspace_id,
                catalog_id=gemini,
            )
        )
    assert not (await case.service(1).get()).available


@pytest.mark.anyio
async def test_credential_rotation_keeps_version_and_removal_disables(embedding_case):
    case = embedding_case
    _, secret_id = await case.connect()
    request = await case.request()
    async with case.sessions.begin() as session:
        secret = await session.scalar(
            select(OrganizationSecret).where(OrganizationSecret.id == secret_id)
        )
        secret.encrypted_keys = encrypted({"OPENAI_API_KEY": "synthetic-rotated"})
    assert (await case.request()).config_version == request.config_version
    await embed_current(request, case.client)
    async with case.sessions.begin() as session:
        await session.execute(
            delete(OrganizationSecret).where(OrganizationSecret.id == secret_id)
        )
    assert await resolve_embedding_configuration(case.scope()) is None
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == EmbeddingErrorCode.NOT_CONFIGURED
    async with case.sessions.begin() as session:
        await session.execute(
            delete(AgentCatalog).where(
                AgentCatalog.organization_id == case.scope().organization_id
            )
        )
    await case.connect()
    assert (await case.request()).config_version > request.config_version


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_provider_failure_does_not_select_another_provider(
    embedding_case, status
):
    case = embedding_case
    await case.connect("openai")
    await case.connect("gemini")
    request = await case.request()
    case.server.status = status
    with pytest.raises(EmbeddingError):
        await embed_current(request, case.client)
    assert case.server.calls == ["/v1/embeddings"]
    assert (await case.request()).config_version == request.config_version


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["remove", "preference", "region"])
async def test_changes_during_provider_call_reject_stale_results(
    embedding_case, change
):
    case = embedding_case
    _, secret_id = await case.connect("bedrock" if change == "region" else "openai")
    gemini, _ = await case.connect("gemini")
    if change == "region":
        async with case.sessions() as session:
            catalog = await session.scalar(
                select(AgentCatalog.id).where(
                    AgentCatalog.organization_id == case.scope().organization_id,
                    AgentCatalog.model_provider == "bedrock",
                )
            )
        await case.prefer(catalog)
    request = await case.request()
    case.server.hold = True
    task = asyncio.create_task(embed_current(request, case.client))
    try:
        assert await asyncio.to_thread(case.server.entered.wait, 3)
        if change == "preference":
            await asyncio.wait_for(case.prefer(gemini), 2)
        else:
            async with case.sessions.begin() as session:
                secret = await asyncio.wait_for(
                    session.scalar(
                        select(OrganizationSecret)
                        .where(OrganizationSecret.id == secret_id)
                        .with_for_update()
                    ),
                    2,
                )
                if change == "remove":
                    await session.delete(secret)
                else:
                    secret.encrypted_keys = encrypted(
                        {
                            "AWS_BEARER_TOKEN_BEDROCK": "synthetic",
                            "AWS_REGION": "us-west-2",
                        }
                    )
    finally:
        case.server.release.set()
    with pytest.raises(EmbeddingError) as caught:
        await task
    assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_CHANGED


@pytest.mark.anyio
async def test_invalid_preferred_credential_does_not_fall_back(embedding_case):
    case = embedding_case
    await case.connect("openai", values={"OPENAI_API_KEY": ""})
    await case.connect("gemini")
    with pytest.raises(EmbeddingError) as caught:
        await resolve_embedding_configuration(case.scope())
    assert caught.value.code == EmbeddingErrorCode.CREDENTIAL_INVALID
    assert not case.server.calls


@pytest.mark.anyio
async def test_status_requires_workspace_read_but_not_secret_read(embedding_case):
    case = embedding_case
    await case.connect()
    assert (await case.service().get()).available
    role = case.roles[0].model_copy(update={"scopes": frozenset()})
    with pytest.raises(ScopeDeniedError):
        await WorkspaceEmbeddingService(role).get()


@pytest.mark.anyio
async def test_provider_reconnection_preserves_operational_pause(embedding_case):
    case = embedding_case
    _, secret_id = await case.connect()
    await case.request()
    async with case.sessions.begin() as session:
        state = await session.get(
            SearchWorkspaceState,
            (case.scope().organization_id, case.scope().workspace_id),
        )
        state.state = SearchState.PAUSED
        secret = await session.scalar(
            select(OrganizationSecret).where(OrganizationSecret.id == secret_id)
        )
        # Hide the credential from selection without changing model access.
        secret.environment = "synthetic-other-environment"
    assert await resolve_embedding_configuration(case.scope()) is None
    async with case.sessions.begin() as session:
        secret = await session.scalar(
            select(OrganizationSecret).where(OrganizationSecret.id == secret_id)
        )
        secret.environment = "default"
    request = await case.request()
    assert (await case.service().get()).state == SearchState.PAUSED
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == EmbeddingErrorCode.NOT_CONFIGURED
    assert not case.server.calls
