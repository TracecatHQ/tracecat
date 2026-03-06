"""Unit tests for persisted MCP catalog artifact execution service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest
from cryptography.fernet import Fernet
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    PromptMessage,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl
from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    Membership,
    OAuthIntegration,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.enums import (
    MCPAuthType,
    MCPCatalogArtifactType,
    OAuthGrantType,
)
from tracecat.mcp.catalog import artifact_service as artifact_service_module
from tracecat.mcp.catalog.artifact_service import MCPCatalogArtifactService
from tracecat.secrets.encryption import decrypt_value, encrypt_value

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


class _FakeIntegrationService:
    def __init__(self, *, session: AsyncSession, role: Role) -> None:
        self.session = session
        self.role = role

    def _decrypt_token(self, encrypted_token: bytes) -> str | None:
        return decrypt_value(encrypted_token, key=TEST_ENCRYPTION_KEY).decode("utf-8")

    async def refresh_token_if_needed(
        self, integration: OAuthIntegration
    ) -> OAuthIntegration:
        return integration

    async def get_access_token(self, integration: OAuthIntegration) -> Any:
        token = self._decrypt_token(integration.encrypted_access_token)
        if token is None:
            return None
        from pydantic import SecretStr

        return SecretStr(token)


@pytest.fixture(autouse=True)
def patch_integration_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        artifact_service_module,
        "IntegrationService",
        _FakeIntegrationService,
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Artifact Org",
        slug=f"artifact-org-{uuid.uuid4().hex[:8]}",
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email="artifact@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization, user: User) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Artifact Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.flush()
    session.add(
        Membership(
            user_id=user.id,
            workspace_id=workspace.id,
        )
    )
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
def org_admin_role(org: Organization, user: User, workspace: Workspace) -> Role:
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        service_id="tracecat-mcp",
        scopes=frozenset({"org:workspace:read", "integration:read"}),
    )


async def _create_oauth_integration(
    *,
    session: AsyncSession,
    workspace: Workspace,
    user: User,
    access_token: str = "oauth-token",
) -> OAuthIntegration:
    integration = OAuthIntegration(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        provider_id="github",
        encrypted_access_token=encrypt_value(
            access_token.encode("utf-8"), key=TEST_ENCRYPTION_KEY
        ),
        encrypted_refresh_token=None,
        encrypted_client_id=encrypt_value(b"client-id", key=TEST_ENCRYPTION_KEY),
        encrypted_client_secret=encrypt_value(
            b"client-secret", key=TEST_ENCRYPTION_KEY
        ),
        use_workspace_credentials=False,
        token_type="Bearer",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope=None,
        requested_scopes=None,
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        authorization_endpoint="https://github.com/login/oauth/authorize",
        token_endpoint="https://github.com/login/oauth/access_token",
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def _create_mcp_integration(
    *,
    session: AsyncSession,
    workspace: Workspace,
    oauth_integration: OAuthIntegration | None = None,
    auth_type: MCPAuthType = MCPAuthType.NONE,
    server_type: str = "http",
    custom_headers: dict[str, str] | None = None,
) -> MCPIntegration:
    encrypted_headers = None
    if custom_headers is not None:
        encrypted_headers = encrypt_value(
            orjson.dumps(custom_headers),
            key=TEST_ENCRYPTION_KEY,
        )
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Artifact MCP",
        description="Artifact MCP",
        slug=f"artifact-mcp-{uuid.uuid4().hex[:6]}",
        scope_namespace="mcpartifact00001",
        server_type=server_type,
        server_uri="https://api.example.com/mcp" if server_type == "http" else None,
        auth_type=auth_type,
        oauth_integration_id=oauth_integration.id if oauth_integration else None,
        encrypted_headers=encrypted_headers,
        stdio_command=None if server_type == "http" else "npx",
        stdio_args=None,
        encrypted_stdio_env=None,
        timeout=30,
        discovery_status="succeeded",
        catalog_version=1,
        sandbox_allow_network=False,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def _insert_catalog_entry(
    *,
    session: AsyncSession,
    integration: MCPIntegration,
    artifact_type: MCPCatalogArtifactType,
    artifact_key: str,
    artifact_ref: str,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await session.execute(
        insert(MCPIntegrationCatalogEntry).values(
            id=entry_id,
            mcp_integration_id=integration.id,
            workspace_id=integration.workspace_id,
            integration_name=integration.name,
            artifact_type=artifact_type.value,
            artifact_key=artifact_key,
            artifact_ref=artifact_ref,
            display_name=artifact_ref,
            description=f"{artifact_type.value} {artifact_ref}",
            input_schema={"type": "object"},
            artifact_metadata={"origin": "test"},
            raw_payload={"name": artifact_ref},
            content_hash=artifact_key.ljust(64, "0"),
            is_active=True,
            search_vector=func.to_tsvector("simple", artifact_ref),
        )
    )
    await session.commit()
    return entry_id


class _FakeClient:
    def __init__(
        self,
        *,
        call_tool_result: CallToolResult | None = None,
        read_resource_result: list[TextResourceContents] | None = None,
        get_prompt_result: GetPromptResult | None = None,
    ) -> None:
        self.call_tool_result = call_tool_result
        self.read_resource_result = read_resource_result or []
        self.get_prompt_result = get_prompt_result

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        assert self.call_tool_result is not None
        return self.call_tool_result

    async def read_resource(self, uri: str) -> list[TextResourceContents]:
        return self.read_resource_result

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> GetPromptResult:
        assert self.get_prompt_result is not None
        return self.get_prompt_result


@pytest.mark.anyio
class TestMCPCatalogArtifactService:
    async def test_build_headers_combines_custom_headers_and_oauth_token(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
        user: User,
    ) -> None:
        oauth_integration = await _create_oauth_integration(
            session=session,
            workspace=workspace,
            user=user,
            access_token="secret-token",
        )
        integration = await _create_mcp_integration(
            session=session,
            workspace=workspace,
            oauth_integration=oauth_integration,
            auth_type=MCPAuthType.OAUTH2,
            custom_headers={"X-Tracecat": "1"},
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="tool-a1",
            artifact_ref="list_repos",
        )
        service = MCPCatalogArtifactService(session=session, role=org_admin_role)
        target = await service.resolve_artifact(
            workspace_id=workspace.id,
            artifact_ref_or_id=str(entry_id),
            artifact_type=MCPCatalogArtifactType.TOOL,
        )

        headers = await service._build_headers(target)

        assert headers["X-Tracecat"] == "1"
        assert headers["Authorization"] == "Bearer secret-token"

    async def test_execute_tool_returns_remote_call_result(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="tool-a1",
            artifact_ref="list_repos",
        )
        service = MCPCatalogArtifactService(session=session, role=org_admin_role)
        fake_client = _FakeClient(
            call_tool_result=CallToolResult(
                content=[TextContent(type="text", text="ok")],
                structuredContent={"status": "ok"},
                isError=False,
            )
        )

        async def fake_create_remote_client(target: Any) -> _FakeClient:
            return fake_client

        monkeypatch.setattr(
            service,
            "_create_remote_client",
            fake_create_remote_client,
        )

        result = await service.execute_tool(
            workspace_id=workspace.id,
            artifact_ref_or_id=str(entry_id),
            arguments={"owner": "tracecat"},
        )

        assert result.result["structuredContent"] == {"status": "ok"}
        assert result.artifact.artifact_ref == "list_repos"

    async def test_read_resource_truncates_large_content(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="resource-a1",
            artifact_ref="docs://readme",
        )
        service = MCPCatalogArtifactService(session=session, role=org_admin_role)
        fake_client = _FakeClient(
            read_resource_result=[
                TextResourceContents(
                    uri=AnyUrl("https://example.com/readme"),
                    text="a" * 40000,
                )
            ]
        )

        async def fake_create_remote_client(target: Any) -> _FakeClient:
            return fake_client

        monkeypatch.setattr(
            service,
            "_create_remote_client",
            fake_create_remote_client,
        )

        result = await service.read_resource(
            workspace_id=workspace.id,
            artifact_ref_or_id=str(entry_id),
        )

        assert result.truncated is True
        assert result.contents[0]["truncated"] is True
        assert len(result.contents[0]["text"]) == result.max_content_chars

    async def test_get_prompt_preserves_structured_messages(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="prompt-a1",
            artifact_ref="triage_incident",
        )
        service = MCPCatalogArtifactService(session=session, role=org_admin_role)
        fake_client = _FakeClient(
            get_prompt_result=GetPromptResult(
                description="Prompt description",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text="Investigate this"),
                    )
                ],
            )
        )

        async def fake_create_remote_client(target: Any) -> _FakeClient:
            return fake_client

        monkeypatch.setattr(
            service,
            "_create_remote_client",
            fake_create_remote_client,
        )

        result = await service.get_prompt(
            workspace_id=workspace.id,
            artifact_ref_or_id=str(entry_id),
            arguments={"severity": "high"},
        )

        assert result.result["messages"][0]["role"] == "user"
        assert result.result["messages"][0]["content"]["text"] == "Investigate this"

    async def test_stdio_artifacts_are_rejected_for_now(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session,
            workspace=workspace,
            server_type="stdio",
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="tool-a1",
            artifact_ref="list_repos",
        )
        service = MCPCatalogArtifactService(session=session, role=org_admin_role)

        with pytest.raises(TracecatValidationError):
            await service.execute_tool(
                workspace_id=workspace.id,
                artifact_ref_or_id=str(entry_id),
                arguments={"owner": "tracecat"},
            )
