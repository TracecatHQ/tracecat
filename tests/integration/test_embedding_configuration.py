"""Automatic provider selection against PostgreSQL and a local HTTP provider."""

import asyncio
import ipaddress
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tests.embedding_helpers import configuration_for, request_for, wire_response
from tracecat import config
from tracecat.agent.service import AgentManagementService
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
from tracecat.search.embeddings.catalog import recipe_revision
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.service import (
    WorkspaceEmbeddingService,
    embed_current,
    resolve_embedding_configuration,
)
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.types import (
    EmbeddingRequest,
    SearchScope,
    SearchState,
)
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.schemas import SecretKeyValue

pytestmark = pytest.mark.anyio


@dataclass
class ProviderServer:
    status: int = 200
    calls: list[str] = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    hold: bool = False
    models: tuple[str, ...] = ()


@pytest.fixture
def provider_server():
    state = ProviderServer()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(orjson.dumps({"data": [{"id": m} for m in state.models]}))

        def do_POST(self):
            data = orjson.loads(self.rfile.read(int(self.headers["content-length"])))
            state.calls.append(self.path)
            state.entered.set()
            if state.hold:
                state.release.wait(10)
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path.endswith("/api/embed"):
                provider, count = "ollama", len(data["input"])
            elif "batchEmbedContents" in self.path:
                provider, count = "gemini", len(data["requests"])
            elif "invoke" in self.path:
                provider, count = "bedrock", 1
            else:
                provider = "vllm" if "MiniLM" in data["model"] else "openai"
                count = len(data["input"])
            configuration = configuration_for(provider)
            payload = wire_response(
                configuration, [[1.0] * configuration.spec.dimensions] * count
            )
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
            "127.0.0.1",
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
    base_url: str

    def scope(self, index=0):
        role = self.roles[index]
        assert role.organization_id is not None and role.workspace_id is not None
        return SearchScope(role.organization_id, role.workspace_id)

    def service(self, index=0):
        return WorkspaceEmbeddingService(self.roles[index])

    async def state(self, session):
        state = await session.get(
            SearchWorkspaceState,
            (self.scope().organization_id, self.scope().workspace_id),
        )
        assert state is not None
        return state

    async def secret(self, session, secret_id):
        secret = await session.scalar(
            select(OrganizationSecret).where(OrganizationSecret.id == secret_id)
        )
        assert secret is not None
        return secret

    async def saved_configuration(self, session, version):
        record = await session.get(
            SearchEmbeddingConfig,
            (self.scope().organization_id, self.scope().workspace_id, version),
        )
        assert record is not None
        return record

    async def request(self, index=0):
        scope = self.scope(index)
        selected = await resolve_embedding_configuration(scope)
        assert selected is not None
        return replace(request_for(selected, ("synthetic passage",)), scope=scope)

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

    def setting(self, key, value, index=0):
        return OrganizationSetting(
            organization_id=self.scope(index).organization_id,
            key=key,
            value=orjson.dumps(value),
            value_type="json",
        )

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
                self.setting("agent_default_model_catalog_id", str(catalog_id), index)
            )


@pytest.fixture
async def case(monkeypatch, provider_server) -> AsyncIterator[ConfigurationCase]:
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url)
    monkeypatch.setattr(
        config,
        "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS",
        [ipaddress.ip_network("127.0.0.0/8")],
    )
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
        yield ConfigurationCase(
            roles, EmbeddingClient(http), state, sessions, f"http://127.0.0.1:{port}/v1"
        )
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


@pytest.mark.parametrize(
    "provider,dimensions", [("openai", 1536), ("gemini", 3072), ("bedrock", 1024)]
)
async def test_automatically_reuses_existing_provider(case, provider, dimensions):
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


async def test_no_supported_provider_is_unavailable_without_network_or_state(
    case,
):
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


async def test_default_provider_and_fixed_fallback_order(case):
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
        state = await case.state(session)
        assert state.reconciliation_required


async def test_workspace_access_overrides_and_cross_org_isolation(case):
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


async def test_credential_rotation_keeps_version_and_removal_disables(case):
    _, secret_id = await case.connect()
    request = await case.request()
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
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


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_provider_failure_does_not_select_another_provider(case, status):
    await case.connect("openai")
    await case.connect("gemini")
    request = await case.request()
    case.server.status = status
    with pytest.raises(EmbeddingError):
        await embed_current(request, case.client)
    assert case.server.calls == ["/v1/embeddings"]
    assert (await case.request()).config_version == request.config_version


@pytest.mark.parametrize("change", ["remove", "preference", "region"])
async def test_changes_during_provider_call_reject_stale_results(case, change):
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


async def test_invalid_preferred_credential_does_not_fall_back(case):
    await case.connect("openai", values={"OPENAI_API_KEY": ""})
    await case.connect("gemini")
    with pytest.raises(EmbeddingError) as caught:
        await resolve_embedding_configuration(case.scope())
    assert caught.value.code == EmbeddingErrorCode.CREDENTIAL_INVALID
    assert not case.server.calls


async def test_status_requires_workspace_read_but_not_secret_read(case):
    await case.connect()
    assert (await case.service().get()).available
    role = case.roles[0].model_copy(update={"scopes": frozenset()})
    with pytest.raises(ScopeDeniedError):
        await WorkspaceEmbeddingService(role).get()


async def test_provider_reconnection_preserves_operational_pause(case):
    _, secret_id = await case.connect()
    await case.request()
    async with case.sessions.begin() as session:
        state = await case.state(session)
        state.state = SearchState.PAUSED
        secret = await case.secret(session, secret_id)
        # Hide the credential from selection without changing model access.
        secret.environment = "synthetic-other-environment"
    assert await resolve_embedding_configuration(case.scope()) is None
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
        secret.environment = "default"
    request = await case.request()
    assert (await case.service().get()).state == SearchState.PAUSED
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == EmbeddingErrorCode.NOT_CONFIGURED
    assert not case.server.calls


@pytest.mark.parametrize(
    "base_url",
    [
        "https://proxy.example.com/v1",
        "https://api.openai.com/v1/proxy",
        "http://api.openai.com/v1",
    ],
)
async def test_custom_openai_base_url_never_sends_credentials(case, base_url):
    _, secret_id = await case.connect()
    await case.connect("gemini")  # Rejection must not silently switch providers.
    request = await case.request()
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
        secret.encrypted_keys = encrypted(
            {"OPENAI_API_KEY": "synthetic-proxy-key", "OPENAI_BASE_URL": base_url}
        )
    for operation in (
        case.service().get(),
        resolve_embedding_configuration(case.scope()),
        embed_current(request, case.client),
    ):
        with pytest.raises(EmbeddingError) as caught:
            await operation
        assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_INVALID
        assert caught.value.__context__ is None
        assert "synthetic-proxy-key" not in str(caught.value)
    assert not case.server.calls


@pytest.mark.parametrize(
    "base_url", ["", "https://api.openai.com/v1", "https://api.openai.com/v1/"]
)
async def test_standard_openai_base_url_remains_supported(case, base_url):
    await case.connect(
        values={"OPENAI_API_KEY": "synthetic-openai", "OPENAI_BASE_URL": base_url}
    )
    result = await embed_current(await case.request(), case.client)
    assert len(result.results[0].vector) == 1536
    assert len(case.server.calls) == 1


@pytest.mark.parametrize(
    "scenario,expected_default",
    [
        ("canonical", "gemini"),
        ("malformed_id", "gemini"),
        ("disabled_id", None),
        ("ambiguous_name", None),
        ("disabled_duplicate", "gemini"),
    ],
)
async def test_agents_and_search_share_default_model_resolution(
    case, scenario, expected_default
):
    openai_id, _ = await case.connect("openai")
    gemini_id, _ = await case.connect("gemini")
    async with case.sessions.begin() as session:
        session.add(case.setting("agent_default_model", "synthetic-gemini-chat"))
        if scenario in {"canonical", "malformed_id", "disabled_id"}:
            catalog_id = "invalid-id" if scenario == "malformed_id" else str(gemini_id)
            session.add(case.setting("agent_default_model_catalog_id", catalog_id))
        if scenario == "disabled_id":
            await session.execute(
                delete(AgentModelAccess).where(AgentModelAccess.catalog_id == gemini_id)
            )
        if scenario == "ambiguous_name":
            entry = await session.scalar(
                select(AgentCatalog).where(AgentCatalog.id == openai_id)
            )
            entry.model_name = "synthetic-gemini-chat"
        if scenario == "disabled_duplicate":
            # This alphabetically earlier match is not enabled and must be ignored.
            session.add(
                AgentCatalog(
                    organization_id=case.scope().organization_id,
                    model_provider="anthropic",
                    model_name="synthetic-gemini-chat",
                )
            )
    agent_role = case.roles[0].model_copy(update={"scopes": frozenset({"agent:read"})})
    async with case.sessions() as session:
        selected = await AgentManagementService(
            session, role=agent_role
        ).get_default_model_selection()
    assert (selected.model_provider if selected else None) == expected_default
    status = await case.service().get()
    assert status.configuration.provider == (expected_default or "openai")
    assert not case.server.calls


async def test_agent_default_lookup_still_requires_agent_read(case):
    async with case.sessions() as session:
        service = AgentManagementService(session, role=case.roles[0])
        with pytest.raises(ScopeDeniedError):
            await service.get_default_model_selection()


@pytest.mark.parametrize(
    "change,paused,provider_removed",
    [
        ("legacy_recipe", False, False),
        ("old_recipe", False, False),
        ("retired_model", False, False),
        ("retired_model", True, False),
        ("retired_model", False, True),
        ("retired_model", True, True),
    ],
)
async def test_saved_configuration_change_rebuilds_or_disables(
    case, change, paused, provider_removed
):
    _, secret_id = await case.connect()
    request = await case.request()
    async with case.sessions.begin() as session:
        config = await case.saved_configuration(session, request.config_version)
        if change == "retired_model":
            config.model = "retired-embedding-model"
        else:
            config.recipe_revision = None if change == "legacy_recipe" else "old-recipe"
        state = await case.state(session)
        state.state = SearchState.PAUSED if paused else SearchState.ACTIVE
        state.reconciliation_required = False
        if provider_removed:
            await session.execute(
                delete(OrganizationSecret).where(OrganizationSecret.id == secret_id)
            )
    status = await case.service().get()
    assert status.available is not provider_removed
    assert status.reindex_required
    selected = await resolve_embedding_configuration(case.scope())
    if provider_removed:
        assert selected is None
    else:
        assert selected is not None and selected.version > request.config_version
        assert selected.spec.model == "text-embedding-3-small"
        assert selected.recipe_revision == recipe_revision(selected.spec)
    async with case.sessions() as session:
        state = await case.state(session)
        version = state.current_version
        assert version > request.config_version
        assert state.reconciliation_required
        expected_state = (
            SearchState.PAUSED
            if paused
            else (SearchState.DISABLED if provider_removed else SearchState.ACTIVE)
        )
        assert state.state == expected_state
    await resolve_embedding_configuration(case.scope())
    async with case.sessions() as session:
        state = await case.state(session)
        assert state.current_version == version
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == (
        EmbeddingErrorCode.NOT_CONFIGURED
        if paused or provider_removed
        else EmbeddingErrorCode.CONFIGURATION_CHANGED
    )
    assert (await case.service().get()).reindex_required
    assert not case.server.calls


@pytest.mark.parametrize(
    "provider,model",
    [
        ("ollama", "all-minilm:latest"),
        ("vllm", "sentence-transformers/all-MiniLM-L6-v2"),
    ],
)
async def test_self_hosted_discovery_selection_embedding_and_removal(
    case, provider, model, monkeypatch
):
    await case.connect(provider, values={f"{provider.upper()}_BASE_URL": case.base_url})
    assert not (await case.service().get()).available  # Chat alone is insufficient.
    case.server.models = (model, "synthetic-chat")
    admin = case.roles[0].model_copy(
        update={"scopes": frozenset({"agent:read", "agent:update", "org:secret:read"})}
    )
    async with case.sessions() as session:
        await AgentManagementService(
            session, role=admin
        ).refresh_gateway_provider_catalog(provider)
    case.server.calls.clear()
    status = await case.service().get()
    assert status.available and status.configuration.dimensions == 384
    assert not case.server.calls  # Selection/status never probes the provider.
    request = await case.request()
    with monkeypatch.context() as policy:
        policy.setattr(config, "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS", [])
        with pytest.raises(EmbeddingError) as caught:
            await embed_current(request, case.client)
        assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_INVALID
        assert caught.value.__context__ is None
        assert not case.server.calls  # Denied destinations never receive text or keys.
    result = await embed_current(request, case.client)
    assert len(result.results[0].vector) == 384
    assert case.server.calls == (
        ["/api/embed"] if provider == "ollama" else ["/v1/embeddings"]
    )

    # Refresh removes the embedding model but leaves chat usable.
    case.server.models = ("synthetic-chat",)
    async with case.sessions() as session:
        await AgentManagementService(
            session, role=admin
        ).refresh_gateway_provider_catalog(provider)
    assert await resolve_embedding_configuration(case.scope()) is None
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(request, case.client)
    assert caught.value.code == EmbeddingErrorCode.NOT_CONFIGURED


@pytest.mark.parametrize(
    "provider,model",
    [
        ("ollama", "all-minilm:22m"),
        ("vllm", "sentence-transformers/all-MiniLM-L6-v2"),
    ],
)
async def test_self_hosted_model_access_and_endpoint_versioning(case, provider, model):
    chat_id, secret_id = await case.connect(
        provider, values={f"{provider.upper()}_BASE_URL": case.base_url}
    )
    await case.connect("openai")
    await case.prefer(chat_id)
    async with case.sessions.begin() as session:
        catalog = AgentCatalog(
            organization_id=case.scope().organization_id,
            model_provider=provider,
            model_name=model,
        )
        session.add(catalog)
        await session.flush()
        model_id = catalog.id
        session.add(
            AgentModelAccess(
                organization_id=case.scope().organization_id, catalog_id=model_id
            )
        )
    selected = await resolve_embedding_configuration(case.scope())
    assert selected is not None
    assert selected.spec.provider == provider  # Default provider beats cloud fallback.
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
        secret.encrypted_keys = encrypted(
            {
                f"{provider.upper()}_BASE_URL": case.base_url,
                f"{provider.upper()}_API_KEY": "synthetic-rotated",
            }
        )
    rotated = await resolve_embedding_configuration(case.scope())
    assert rotated is not None and rotated.version == selected.version
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
        secret.encrypted_keys = encrypted(
            {f"{provider.upper()}_BASE_URL": case.base_url.replace("/v1", "/new/v1")}
        )
    moved = await resolve_embedding_configuration(case.scope())
    assert moved is not None and moved.version > selected.version
    # Explicit workspace chat access must not imply embedding-model access.
    async with case.sessions.begin() as session:
        session.add(
            AgentModelAccess(
                organization_id=case.scope().organization_id,
                workspace_id=case.scope().workspace_id,
                catalog_id=chat_id,
            )
        )
    assert await resolve_embedding_configuration(case.scope()) is None
    # Another tenant's catalog/credentials never enable this workspace.
    assert await resolve_embedding_configuration(case.scope(1)) is None


async def test_unused_corrupt_credentials_do_not_break_selection(case):
    await case.connect("openai")
    _, secret_id = await case.connect("gemini")
    async with case.sessions.begin() as session:
        secret = await case.secret(session, secret_id)
        secret.encrypted_keys = b"corrupt-lower-priority-credential"
    selected = await resolve_embedding_configuration(case.scope())
    assert selected is not None and selected.spec.provider == "openai"
