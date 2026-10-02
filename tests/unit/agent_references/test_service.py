"""Lookup and policy boundaries use stored metadata, not native runtimes."""

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.mcp.utils import REGISTRY_MCP_SERVER_NAME, normalize_mcp_tool_name
from tracecat.agent.references.compiler import compile_references
from tracecat.agent.references.compiler_types import (
    GraphLimits,
    ReferenceLookupError,
    ScopePolicy,
)
from tracecat.agent.references.contracts import (
    AuthoredReferenceInput,
    AuthoredSource,
    ExecutionAuthority,
    ExplicitDeclarations,
)
from tracecat.agent.references.service import ReferenceService
from tracecat.agent.references.uri import ReferenceDiagnosticCode as Code
from tracecat.agent.references.uri import ReferenceKind as Kind
from tracecat.agent.references.uri import ReferenceTarget
from tracecat.agent.skill.schemas import (
    SkillDraftFileRead,
    SkillFileEntry,
    SkillVersionRead,
)
from tracecat.agent.skill.service import SkillService
from tracecat.agent.tools import EXCLUDED_AGENT_ACTIONS
from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    Skill,
    SkillVersion,
    Workflow,
    WorkflowDefinition,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        scopes=frozenset({"*"}),
    )
    svc = ReferenceService(AsyncMock(spec=AsyncSession), role=role)
    svc.require_entitlement = AsyncMock()
    return svc


def integration(server_type="http"):
    return MCPIntegration(
        id=uuid4(),
        slug="example",
        server_type=server_type,
        tools=[
            {
                "name": "read",
                "description": "Read",
                "enabled": True,
                "status": "available",
                "requires_approval": True,
            },
            {
                "name": "write",
                "description": "Write",
                "enabled": False,
                "status": "available",
                "requires_approval": False,
            },
        ],
    )


@pytest.mark.anyio
async def test_mcp_mention_grants_selected_tool_and_retains_approval(
    service, monkeypatch
):
    row = integration()
    get = AsyncMock(return_value=row)
    monkeypatch.setattr(IntegrationService, "get_mcp_integration", get)
    probe = AsyncMock(side_effect=AssertionError("must not discover tools"))
    monkeypatch.setattr(IntegrationService, "_probe_mcp_http_server", probe)
    monkeypatch.setattr(IntegrationService, "resolve_mcp_http_server_config", probe)
    target = ReferenceTarget(Kind.MCP_TOOL, str(row.id), "read")
    selected = await service.resolve(target, version_id=None, limits=GraphLimits())
    assert selected.selected.target == target
    grants = await service.policy((target,), ScopePolicy())
    assert grants.mcp == ((row.id, frozenset({"read"})),)
    key = normalize_mcp_tool_name(
        f"mcp__{REGISTRY_MCP_SERVER_NAME}__mcp__example__read"
    )
    assert dict(grants.approvals)[key] is True
    assert grants.tool_count == 1
    assert get.await_count == 1
    probe.assert_not_awaited()
    service.require_entitlement.assert_awaited_once()


@pytest.mark.anyio
async def test_unavailable_mcp_tool_not_hidden_by_whole_server(service, monkeypatch):
    row = integration()
    monkeypatch.setattr(
        IntegrationService, "get_mcp_integration", AsyncMock(return_value=row)
    )
    with pytest.raises(ReferenceLookupError) as exc:
        await service.resolve(
            ReferenceTarget(Kind.MCP_TOOL, str(row.id), "write"),
            version_id=None,
            limits=GraphLimits(),
        )
    assert exc.value.code == Code.UNRESOLVED


@pytest.mark.anyio
async def test_stdio_whole_server_retains_internet_requirement(service, monkeypatch):
    row = integration("stdio")
    monkeypatch.setattr(
        IntegrationService, "get_mcp_integration", AsyncMock(return_value=row)
    )
    target = ReferenceTarget(Kind.MCP_SERVER, str(row.id))
    grants = await service.policy((target,), ScopePolicy())
    assert grants.internet_access
    assert grants.mcp == ((row.id, None),)
    with pytest.raises(ReferenceLookupError) as exc:
        await service.resolve(
            ReferenceTarget(Kind.MCP_TOOL, str(row.id), "read"),
            version_id=None,
            limits=GraphLimits(),
        )
    assert exc.value.code == Code.UNSUPPORTED_CAPABILITY


@pytest.mark.anyio
async def test_registry_mention_needs_no_manual_declaration(service):
    target = ReferenceTarget(Kind.TOOL, "core.http_request")
    grants = await service.policy(
        (target,), ScopePolicy(approvals=((target.identity, True),))
    )
    assert grants.actions == (target.identity,)
    assert grants.approvals == ((target.identity, True),)
    for policy in [ScopePolicy(namespaces=("tools.",)), ScopePolicy()]:
        denied = (
            target
            if policy.namespaces
            else ReferenceTarget(Kind.TOOL, sorted(EXCLUDED_AGENT_ACTIONS)[0])
        )
        with pytest.raises(ReferenceLookupError) as exc:
            await service.policy((denied,), policy)
        assert exc.value.code == Code.FORBIDDEN
        assert exc.value.target == denied


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,cls,method",
    [
        (Kind.SKILL, SkillService, "get_skill"),
        (Kind.MCP_SERVER, IntegrationService, "get_mcp_integration"),
        (Kind.TOOL, RegistryActionsService, "get_action_from_index"),
    ],
)
async def test_missing_and_inaccessible_are_indistinguishable(
    service, monkeypatch, kind, cls, method
):
    get = AsyncMock(return_value=None)
    monkeypatch.setattr(cls, method, get)
    target = ReferenceTarget(
        kind, "core.http_request" if kind == Kind.TOOL else str(uuid4())
    )
    with pytest.raises(ReferenceLookupError) as missing:
        await service.resolve(target, version_id=None, limits=GraphLimits())
    service.role = service.role.model_copy(update={"scopes": frozenset()})
    get.reset_mock()
    with pytest.raises(ReferenceLookupError) as denied:
        await service.resolve(target, version_id=None, limits=GraphLimits())
    assert str(missing.value) == str(denied.value) == "unresolved"
    get.assert_not_awaited()


@pytest.mark.anyio
async def test_skill_service_reads_exact_markdown_and_compiles_derived_tool(
    service, monkeypatch
):
    sid, vid = uuid4(), uuid4()
    target = ReferenceTarget(Kind.SKILL, str(sid))
    tool = ReferenceTarget(Kind.TOOL, "core.http_request")
    markdown = "[Request](tracecat-ref://v1/tool/core.http_request)"
    digest = hashlib.sha256(markdown.encode()).hexdigest()
    skill = Skill(id=sid, current_version_id=vid, slug="example")
    monkeypatch.setattr(SkillService, "get_skill", AsyncMock(return_value=skill))
    now = datetime.now(UTC)
    published = SkillVersionRead(
        id=vid,
        skill_id=sid,
        workspace_id=service.workspace_id,
        version=3,
        manifest_sha256="a" * 64,
        file_count=1,
        total_size_bytes=len(markdown),
        name="Example",
        created_at=now,
        updated_at=now,
        files=[
            SkillFileEntry(
                path="SKILL.md",
                blob_id=uuid4(),
                sha256=digest,
                size_bytes=len(markdown),
                content_type="text/markdown",
            )
        ],
    )
    read = AsyncMock(return_value=published)
    monkeypatch.setattr(SkillService, "get_version_read", read)
    monkeypatch.setattr(
        SkillService,
        "get_version_file",
        AsyncMock(
            return_value=SkillDraftFileRead(
                kind="inline",
                path="SKILL.md",
                content_type="text/markdown",
                size_bytes=len(markdown),
                sha256=digest,
                text_content=markdown,
            )
        ),
    )
    row = SkillVersion(id=vid, skill_id=sid, tools=[], mcp_tools=[])
    result = MagicMock()
    result.scalar_one.return_value = row
    service.session.execute = AsyncMock(return_value=result)
    monkeypatch.setattr(
        RegistryActionsService,
        "get_action_from_index",
        AsyncMock(return_value=object()),
    )
    root = AuthoredSource(
        origin="agent_instructions",
        owner=ReferenceTarget(Kind.AGENT, str(uuid4())),
        version_id=uuid4(),
        path="instructions.md",
        content_hash=hashlib.sha256(b"").hexdigest(),
        markdown="",
    )
    admitted = AuthoredReferenceInput(
        root=root, explicit=ExplicitDeclarations(targets=(target,))
    )
    authority = ExecutionAuthority(
        actor_id=uuid4(),
        session_mode="delegated_preset",
        activate_references=True,
        action_scope_ceiling=None,
        namespace_ceiling=None,
        admission_policy_hash="a" * 64,
    )
    compiled = await compile_references(admitted, authority, service)
    assert not compiled.diagnostics
    assert compiled.scopes[0].grants.actions == (tool.identity,)
    read.assert_awaited_once_with(skill_id=sid, version_id=vid)
    edge = next(e for e in compiled.scopes[0].edges if e.target == tool)
    assert edge.source == target
    assert edge.location is not None and edge.location.path == "SKILL.md"


@pytest.mark.anyio
async def test_workflow_pin_uses_exact_definition_and_never_normalizes(
    service, monkeypatch
):
    wid = WorkflowUUID.new(uuid4())
    version = uuid4()
    get = AsyncMock(return_value=Workflow(id=wid))
    exact = AsyncMock(return_value=WorkflowDefinition(id=version, workflow_id=wid))
    latest = AsyncMock(side_effect=AssertionError("must not follow latest"))
    monkeypatch.setattr(WorkflowsManagementService, "get_workflow", get)
    monkeypatch.setattr(WorkflowDefinitionsService, "get_definition_by_id", exact)
    monkeypatch.setattr(
        WorkflowDefinitionsService, "get_definition_by_workflow_id", latest
    )
    selected = await service.resolve(
        ReferenceTarget(Kind.WORKFLOW, str(wid)),
        version_id=version,
        limits=GraphLimits(),
    )
    assert selected.selected.version_id == version
    get.assert_awaited_once_with(wid, normalize=False)
    exact.assert_awaited_once_with(version)
    latest.assert_not_awaited()
