"""Live PostgreSQL and local HTTP tests for workspace configuration lifecycles."""

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
    Organization,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Secret,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.schemas import EmbeddingConfigurationSave
from tracecat.search.embeddings.service import WorkspaceEmbeddingService, embed_current
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingModel,
)
from tracecat.search.types import EmbeddingInput, EmbeddingRequest, SearchScope
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.schemas import SecretKeyValue


@dataclass
class ProviderServer:
    status: int = 200
    keys: list[str] = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    hold: bool = False


@pytest.fixture
def provider_server():
    state = ProviderServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = orjson.loads(self.rfile.read(int(self.headers["content-length"])))
            state.keys.append(self.headers["Authorization"])
            state.entered.set()
            if state.hold:
                state.release.wait(10)
            dimensions = 1536 if data["model"] == "text-embedding-3-small" else 3072
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                orjson.dumps(
                    {
                        "model": data["model"],
                        "data": [
                            {"index": i, "embedding": [1.0] * dimensions}
                            for i in reversed(range(len(data["input"])))
                        ],
                        "usage": {"prompt_tokens": 8, "total_tokens": 8},
                    }
                )
            )

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
    """Route the fixed provider destination to a real local HTTP test server."""

    def __init__(self, port: int):
        super().__init__()
        self.port = port

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/embeddings"
        request.url = request.url.copy_with(
            scheme="http", host="127.0.0.1", port=self.port
        )
        return await super().handle_async_request(request)


@dataclass
class ConfigurationCase:
    roles: tuple[Role, ...]
    secrets: tuple[uuid.UUID, uuid.UUID]
    client: EmbeddingClient
    server: ProviderServer
    sessions: async_sessionmaker[AsyncSession]

    def service(self, index=0):
        return WorkspaceEmbeddingService(self.roles[index], self.client)

    def params(
        self, index=0, version=0, model: EmbeddingModel = "text-embedding-3-small"
    ):
        return EmbeddingConfigurationSave(
            provider="openai",
            model=model,
            credential_id=self.secrets[index],
            credential_environment="search",
            expected_version=version,
        )

    def request(self, version=1, dimensions=1536):
        role = self.roles[0]
        assert role.organization_id is not None and role.workspace_id is not None
        text = "synthetic passage"
        return EmbeddingRequest(
            SearchScope(role.organization_id, role.workspace_id),
            version,
            dimensions,
            (EmbeddingInput(0, hashlib.sha256(text.encode()).hexdigest(), text),),
        )


@pytest.fixture
async def embedding_case(
    provider_server, monkeypatch
) -> AsyncIterator[ConfigurationCase]:
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url_sync)
    reset_async_engine()
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    organization_id = uuid.uuid4()
    roles = tuple(
        Role(
            type="user",
            service_id="tracecat-api",
            organization_id=organization_id,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            scopes=frozenset({"workspace:read", "workspace:update", "secret:read"}),
        )
        for _ in range(2)
    )
    secrets = (uuid.uuid4(), uuid.uuid4())
    async with sessions.begin() as session:
        session.add(
            Organization(
                id=organization_id,
                name="Synthetic embedding tests",
                slug=str(organization_id),
            )
        )
        await session.flush()
        for i, role in enumerate(roles):
            session.add(
                Workspace(
                    id=role.workspace_id,
                    organization_id=organization_id,
                    name=f"Synthetic {i}",
                )
            )
        await session.flush()
        for i, role in enumerate(roles):
            session.add(
                Secret(
                    id=secrets[i],
                    workspace_id=role.workspace_id,
                    name="embedding",
                    environment="search",
                    encrypted_keys=encrypt_keyvalues(
                        [
                            SecretKeyValue(
                                key="OPENAI_API_KEY",
                                value=SecretStr(f"synthetic-key-{i}"),
                            )
                        ],
                        key=get_db_encryption_key(),
                    ),
                )
            )
    state, port = provider_server
    async with httpx.AsyncClient(transport=LocalProviderTransport(port)) as http:
        yield ConfigurationCase(roles, secrets, EmbeddingClient(http), state, sessions)
    async with sessions.begin() as session:
        for model in (SearchEmbeddingConfig, SearchWorkspaceState):
            await session.execute(
                delete(model).where(model.organization_id == organization_id)
            )
        await session.execute(
            delete(Workspace).where(Workspace.organization_id == organization_id)
        )
        await session.execute(
            delete(Organization).where(Organization.id == organization_id)
        )
    await engine.dispose()
    await get_async_engine().dispose()
    reset_async_engine()


@pytest.mark.anyio
async def test_two_workspaces_use_distinct_credentials_and_models(embedding_case):
    case = embedding_case
    first = await case.service().save(case.params())
    second = await case.service(1).save(case.params(1, model="text-embedding-3-large"))
    assert first.version == second.version == 1
    assert first.configuration.model != second.configuration.model
    assert case.server.keys == ["Bearer synthetic-key-0", "Bearer synthetic-key-1"]
    assert "synthetic-key" not in first.model_dump_json()
    result = await embed_current(case.request(), case.client)
    assert len(result.results[0].vector) == 1536
    async with case.sessions() as session:
        state = await session.scalar(
            select(SearchWorkspaceState).where(
                SearchWorkspaceState.workspace_id == case.roles[0].workspace_id
            )
        )
        assert state.reconciliation_required


@pytest.mark.anyio
async def test_unauthorized_binding_never_calls_provider(embedding_case):
    case = embedding_case
    params = case.params().model_copy(update={"credential_id": case.secrets[1]})
    with pytest.raises(EmbeddingError) as caught:
        await case.service().save(params)
    assert caught.value.code == EmbeddingErrorCode.CREDENTIAL_INVALID
    params = case.params().model_copy(update={"credential_environment": "workflow-env"})
    with pytest.raises(EmbeddingError):
        await case.service().save(params)
    assert case.server.keys == []
    assert (await case.service().get()).version == 0


@pytest.mark.anyio
@pytest.mark.parametrize("scopes", [{"workspace:update"}, {"secret:read"}])
async def test_setup_requires_both_permissions(embedding_case, scopes):
    case = embedding_case
    role = case.roles[0].model_copy(update={"scopes": frozenset(scopes)})
    with pytest.raises(ScopeDeniedError):
        await WorkspaceEmbeddingService(role, case.client).save(case.params())
    assert not case.server.keys


@pytest.mark.anyio
async def test_failed_validation_preserves_saved_configuration(embedding_case):
    case = embedding_case
    before = await case.service().save(case.params())
    case.server.status = 401
    with pytest.raises(EmbeddingError):
        await case.service().save(
            case.params(version=1, model="text-embedding-3-large")
        )
    assert await case.service().get() == before


@pytest.mark.anyio
async def test_rotation_preserves_semantic_version(embedding_case):
    case = embedding_case
    await case.service().save(case.params())
    replacement_id = uuid.uuid4()
    async with case.sessions.begin() as session:
        state = await session.scalar(
            select(SearchWorkspaceState).where(
                SearchWorkspaceState.workspace_id == case.roles[0].workspace_id
            )
        )
        state.reconciliation_required = False
        session.add(
            Secret(
                id=replacement_id,
                workspace_id=case.roles[0].workspace_id,
                name="rotated",
                environment="search",
                encrypted_keys=encrypt_keyvalues(
                    [
                        SecretKeyValue(
                            key="OPENAI_API_KEY", value=SecretStr("synthetic-rotated")
                        )
                    ],
                    key=get_db_encryption_key(),
                ),
            )
        )
    result = await case.service().save(
        case.params(version=1).model_copy(update={"credential_id": replacement_id})
    )
    assert result.version == 1
    assert result.configuration.credential_id == replacement_id
    await embed_current(case.request(), case.client)
    assert case.server.keys[-1] == "Bearer synthetic-rotated"
    async with case.sessions() as session:
        state = await session.scalar(
            select(SearchWorkspaceState).where(
                SearchWorkspaceState.workspace_id == case.roles[0].workspace_id
            )
        )
        assert not state.reconciliation_required


@pytest.mark.anyio
async def test_disable_invalidates_inflight_request_without_holding_db_lock(
    embedding_case,
):
    case = embedding_case
    await case.service().save(case.params())
    case.server.entered.clear()
    case.server.hold = True
    task = asyncio.create_task(embed_current(case.request(), case.client))
    try:
        assert await asyncio.to_thread(case.server.entered.wait, 3)
        # This must finish while the provider is still blocked: no retained DB lock.
        disabled = await asyncio.wait_for(case.service().disable(1), 2)
        assert disabled.version == 2
        assert disabled.configuration is None
    finally:
        case.server.release.set()
    with pytest.raises(EmbeddingError) as caught:
        await task
    assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_CHANGED
    with pytest.raises(EmbeddingError):
        await embed_current(case.request(), case.client)
    resumed = await case.service().save(case.params(version=2))
    assert resumed.version == 3


@pytest.mark.anyio
async def test_stale_save_cannot_overwrite_new_settings(embedding_case):
    case = embedding_case
    await case.service().save(case.params())
    newer = await case.service().save(
        case.params(version=1, model="text-embedding-3-large")
    )
    assert newer.version == 2
    calls = len(case.server.keys)
    with pytest.raises(EmbeddingError):
        await case.service().save(case.params(version=1))
    assert len(case.server.keys) == calls
    assert await case.service().get() == newer


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["disable", "credential"])
async def test_changes_during_validation_reject_save(embedding_case, mutation):
    case = embedding_case
    before = await case.service().save(case.params())
    case.server.entered.clear()
    case.server.hold = True
    task = asyncio.create_task(
        case.service().save(case.params(version=1, model="text-embedding-3-large"))
    )
    try:
        assert await asyncio.to_thread(case.server.entered.wait, 3)
        if mutation == "disable":
            await asyncio.wait_for(case.service().disable(1), 2)
        else:
            async with case.sessions.begin() as session:
                secret = await session.scalar(
                    select(Secret).where(Secret.id == case.secrets[0])
                )
                secret.encrypted_keys = encrypt_keyvalues(
                    [
                        SecretKeyValue(
                            key="OPENAI_API_KEY", value=SecretStr("synthetic-new-value")
                        )
                    ],
                    key=get_db_encryption_key(),
                )
    finally:
        case.server.release.set()
    with pytest.raises(EmbeddingError) as caught:
        await task
    assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_CHANGED
    after = await case.service().get()
    assert after.configuration is None if mutation == "disable" else after == before


@pytest.mark.anyio
async def test_rotating_bound_secret_value_is_used_on_next_call(embedding_case):
    case = embedding_case
    before = await case.service().save(case.params())
    async with case.sessions.begin() as session:
        secret = await session.scalar(
            select(Secret).where(Secret.id == case.secrets[0])
        )
        secret.encrypted_keys = encrypt_keyvalues(
            [
                SecretKeyValue(
                    key="OPENAI_API_KEY", value=SecretStr("synthetic-new-value")
                )
            ],
            key=get_db_encryption_key(),
        )
    await embed_current(case.request(), case.client)
    assert case.server.keys[-1] == "Bearer synthetic-new-value"
    assert await case.service().get() == before


@pytest.mark.anyio
async def test_deleted_bound_secret_does_not_fall_back(embedding_case):
    case = embedding_case
    await case.service().save(case.params())
    async with case.sessions.begin() as session:
        await session.execute(delete(Secret).where(Secret.id == case.secrets[0]))
    calls = len(case.server.keys)
    with pytest.raises(EmbeddingError) as caught:
        await embed_current(case.request(), case.client)
    assert caught.value.code == EmbeddingErrorCode.CREDENTIAL_INVALID
    assert len(case.server.keys) == calls


@pytest.mark.anyio
async def test_model_change_invalidates_inflight_embedding(embedding_case):
    case = embedding_case
    await case.service().save(case.params())
    case.server.entered.clear()
    case.server.hold = True
    task = asyncio.create_task(embed_current(case.request(), case.client))
    try:
        assert await asyncio.to_thread(case.server.entered.wait, 3)
        case.server.hold = False  # The already-blocked request stays blocked.
        changed = await asyncio.wait_for(
            case.service().save(case.params(version=1, model="text-embedding-3-large")),
            3,
        )
        assert changed.version == 2
    finally:
        case.server.release.set()
    with pytest.raises(EmbeddingError) as caught:
        await task
    assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_CHANGED
    fresh = await embed_current(case.request(version=2, dimensions=3072), case.client)
    assert len(fresh.results[0].vector) == 3072


@pytest.mark.anyio
@pytest.mark.parametrize("scopes", [{"workspace:read"}, {"secret:read"}])
async def test_read_requires_workspace_and_secret_permissions(embedding_case, scopes):
    case = embedding_case
    await case.service().save(case.params())
    role = case.roles[0].model_copy(update={"scopes": frozenset(scopes)})
    with pytest.raises(ScopeDeniedError):
        await WorkspaceEmbeddingService(role, case.client).get()
    settings = await case.service().get()
    assert settings.configuration.credential_id == case.secrets[0]
    assert settings.configuration.credential_environment == "search"


@pytest.mark.anyio
async def test_disable_without_secret_permission_preserves_configuration(
    embedding_case,
):
    case = embedding_case
    before = await case.service().save(case.params())
    role = case.roles[0].model_copy(update={"scopes": frozenset({"workspace:update"})})
    with pytest.raises(ScopeDeniedError):
        await WorkspaceEmbeddingService(role, case.client).disable(before.version)
    assert await case.service().get() == before
