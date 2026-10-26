"""Remote MCP header expressions resolve only at the outbound boundary."""

import base64
import contextlib
import traceback
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.mcp import trusted_server, user_client
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role, ctx_secret_masks
from tracecat.db.models import MCPIntegration, Secret
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_headers import resolve_mcp_header_values
from tracecat.integrations.mcp_validation import MCPConfigurationError
from tracecat.integrations.schemas import (
    MCPHttpIntegrationCreate,
    MCPHttpIntegrationTestConnectionRequest,
)
from tracecat.integrations.service import (
    IntegrationService,
    MCPConnectionVerificationError,
)
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService

pytestmark = pytest.mark.anyio

CANARY = "synthetic-mcp-header-secret"
HEADERS = {
    "Authorization": "Bearer ${{ SECRETS.mcp_api.API_KEY }}",
    "X-Literal": "literal-value",
}


@pytest.fixture
def header_service(session: AsyncSession, svc_role: Role) -> IntegrationService:
    return IntegrationService(session, role=svc_role)


@pytest.fixture
async def header_secret(svc_role: Role) -> Secret:
    async with SecretsService.with_session(role=svc_role) as service:
        return await service.create_secret(
            SecretCreate.from_strings("mcp_api", [f"API_KEY={CANARY}"])
        )


@pytest.fixture
async def header_integration(header_service: IntegrationService) -> MCPIntegration:
    return await header_service.create_mcp_integration(
        params=MCPHttpIntegrationCreate(
            name="Templated MCP",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
            custom_credentials=SecretStr(orjson.dumps(HEADERS).decode()),
        )
    )


async def test_custom_headers_resolve_without_changing_storage_or_refs(
    header_service: IntegrationService,
    header_integration: MCPIntegration,
    header_secret: Secret,
) -> None:
    encrypted = header_integration.encrypted_headers
    preset_service = AgentPresetService(
        header_service.session, role=header_service.role
    )
    refs = await preset_service.resolve_mcp_integration_refs(
        [str(header_integration.id)]
    )
    assert refs is not None
    assert all("headers" not in ref for ref in refs)
    assert CANARY not in orjson.dumps(refs).decode()

    config = await header_service.resolve_mcp_http_server_config(header_integration)

    assert config.get("headers") == {
        "Authorization": f"Bearer {CANARY}",
        "X-Literal": "literal-value",
    }
    assert header_integration.encrypted_headers == encrypted
    assert header_service._decrypt_mcp_custom_headers(header_integration) == HEADERS


async def test_secret_rotation_is_visible_on_the_next_resolution(
    header_service: IntegrationService,
    header_integration: MCPIntegration,
    header_secret: Secret,
) -> None:
    initial = await header_service.resolve_mcp_http_server_config(header_integration)
    async with SecretsService.with_session(role=header_service.role) as service:
        secret = await service.get_secret(header_secret.id)
        await service.update_secret(
            secret,
            SecretUpdate(
                keys=[SecretKeyValue.from_str("API_KEY=rotated-synthetic-token")]
            ),
        )
    rotated = await header_service.resolve_mcp_http_server_config(header_integration)

    assert initial.get("headers", {})["Authorization"] == f"Bearer {CANARY}"
    assert rotated.get("headers", {})["Authorization"] == (
        "Bearer rotated-synthetic-token"
    )
    assert header_service._decrypt_mcp_custom_headers(header_integration) == HEADERS


async def test_headers_resolve_secrets_and_variables_in_default_environment(
    header_service: IntegrationService,
    header_secret: Secret,
) -> None:
    async with SecretsService.with_session(role=header_service.role) as service:
        await service.create_secret(
            SecretCreate(
                name="mcp_api",
                environment="production",
                keys=[SecretKeyValue.from_str("API_KEY=other-environment-token")],
            )
        )
    async with VariablesService.with_session(role=header_service.role) as service:
        for environment, tenant in (
            ("default", "default-tenant"),
            ("production", "other-tenant"),
        ):
            await service.create_variable(
                VariableCreate(
                    name="mcp_config",
                    values={"tenant": tenant},
                    environment=environment,
                )
            )

    resolved = await resolve_mcp_header_values(
        {
            **HEADERS,
            "X-Tenant": "${{ VARS.mcp_config.tenant }}",
            "X-API-Key": "${{ SECRETS.mcp_api.API_KEY }}",
        },
        role=header_service.role,
    )
    assert resolved["Authorization"] == f"Bearer {CANARY}"
    assert resolved["X-API-Key"] == CANARY
    assert resolved["X-Tenant"] == "default-tenant"


async def test_header_resolution_uses_explicit_workspace_not_ambient_role(
    header_service: IntegrationService,
    header_secret: Secret,
    test_role: Role,
) -> None:
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            SecretCreate.from_strings("mcp_api", ["API_KEY=other-workspace-token"])
        )
    token = ctx_role.set(test_role)
    try:
        resolved = await resolve_mcp_header_values(HEADERS, role=header_service.role)
    finally:
        ctx_role.reset(token)
    assert resolved["Authorization"] == f"Bearer {CANARY}"


async def test_foreign_workspace_secret_cannot_satisfy_reference(
    header_service: IntegrationService, test_role: Role
) -> None:
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            SecretCreate.from_strings("mcp_api", [f"API_KEY={CANARY}"])
        )
    with pytest.raises(MCPConfigurationError, match="default environment") as exc_info:
        await resolve_mcp_header_values(HEADERS, role=header_service.role)
    assert exc_info.value.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(exc_info.value))


@pytest.mark.parametrize(
    "expression",
    [
        "${{ SECRETS.mcp_api.MISSING }}",
        "${{ FN.int(SECRETS.mcp_api.API_KEY) }}",
        "${{ FN.int(FN.to_base64(SECRETS.mcp_api.API_KEY)) }}",
    ],
)
async def test_expression_failures_have_masked_chainless_errors(
    expression: str, header_secret: Secret, header_service: IntegrationService
) -> None:
    with pytest.raises(MCPConfigurationError) as exc_info:
        await resolve_mcp_header_values(
            {"X-API-Key": expression}, role=header_service.role
        )

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    message = "".join(traceback.format_exception(exc_info.value))
    assert CANARY not in message
    assert base64.b64encode(CANARY.encode()).decode() not in message


@pytest.mark.parametrize("expression", ["${{ 1 }}", "${{ SECRETS.mcp_api }}"])
async def test_non_string_header_values_fail_without_echoing_values(
    expression: str, header_secret: Secret, header_service: IntegrationService
) -> None:
    with pytest.raises(
        MCPConfigurationError, match="header values must be strings"
    ) as exc_info:
        await resolve_mcp_header_values(
            {"X-API-Key": expression}, role=header_service.role
        )
    assert CANARY not in str(exc_info.value)


async def test_header_names_and_literal_values_remain_unchanged(
    header_service: IntegrationService,
) -> None:
    headers = {"${{ SECRETS.missing.KEY }}": "literal-token", "X-Empty": ""}
    assert await resolve_mcp_header_values(headers, role=header_service.role) == headers


@pytest.mark.parametrize("saved", [False, True])
async def test_api_connection_test_resolves_headers_before_discovery(
    saved: bool,
    header_service: IntegrationService,
    header_integration: MCPIntegration,
    header_secret: Secret,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(user_client, "list_remote_mcp_tools", discover)
    if saved:
        await header_service.verify_mcp_integration(mcp_integration=header_integration)
    else:
        response = await header_service.test_mcp_connection(
            params=MCPHttpIntegrationTestConnectionRequest(
                server_uri="https://mcp.example.com/mcp",
                auth_type=MCPAuthType.CUSTOM,
                custom_credentials=SecretStr(orjson.dumps(HEADERS).decode()),
            )
        )
        assert response.success

    discover.assert_awaited_once()
    assert discover.call_args.args[0]["headers"]["Authorization"] == f"Bearer {CANARY}"
    assert header_service._decrypt_mcp_custom_headers(header_integration) == HEADERS


async def test_gateway_resolves_templates_immediately_before_tool_call(
    header_service: IntegrationService,
    header_integration: MCPIntegration,
    header_secret: Secret,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextlib.asynccontextmanager
    async def preset_session(*, role: Role) -> AsyncIterator[AgentPresetService]:
        yield AgentPresetService(header_service.session, role=role)

    monkeypatch.setattr(AgentPresetService, "with_session", preset_session)
    client = AsyncMock()
    client.call_tool.return_value = "ok"
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(trusted_server, "UserMCPClient", client_factory)
    claims = MCPTokenClaims(
        workspace_id=header_service.workspace_id,
        organization_id=header_service.organization_id,
        session_id=uuid.uuid4(),
        allowed_actions=[f"mcp__{header_integration.slug}__lookup"],
        user_mcp_servers=[
            UserMCPServerClaim(name=header_integration.slug, id=header_integration.id)
        ],
    )
    assert CANARY not in claims.model_dump_json()

    result = await trusted_server._execute_user_mcp(
        header_integration.slug, "lookup", {}, claims
    )

    assert result == "ok"
    config = client_factory.call_args.args[0][0]
    assert config["headers"]["Authorization"] == f"Bearer {CANARY}"
    client.call_tool.assert_awaited_once_with(header_integration.slug, "lookup", {})
    assert CANARY not in claims.model_dump_json()


async def test_api_probe_masks_transport_errors_and_drops_exception_chains(
    header_service: IntegrationService,
    header_integration: MCPIntegration,
    header_secret: Secret,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discover = AsyncMock(side_effect=ValueError(f"Invalid header: Bearer {CANARY}"))
    monkeypatch.setattr(user_client, "list_remote_mcp_tools", discover)
    original_masks = ctx_secret_masks.get()
    with pytest.raises(MCPConnectionVerificationError) as exc_info:
        await header_service._probe_mcp_http_server(header_integration)

    assert CANARY not in "".join(traceback.format_exception(exc_info.value))
    assert CANARY not in (exc_info.value.error or "")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert ctx_secret_masks.get() is original_masks
