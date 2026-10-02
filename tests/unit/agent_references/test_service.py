"""Lookup and policy boundaries use stored metadata, not native runtimes."""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.mcp.utils import REGISTRY_MCP_SERVER_NAME, normalize_mcp_tool_name
from tracecat.agent.preset.service import AgentPresetService
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
    SkillFileEntry,
    SkillVersionRead,
)
from tracecat.agent.skill.service import SkillService
from tracecat.agent.tools import EXCLUDED_AGENT_ACTIONS
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    MCPIntegration,
    Skill,
    SkillBlob,
    Workflow,
    WorkflowDefinition,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.storage import blob
from tracecat.tables.service import TablesService
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
async def test_registry_tool_policy_boundaries(service):
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
        (Kind.AGENT, AgentPresetService, "resolve_agent_preset_version"),
        (Kind.WORKFLOW, WorkflowsManagementService, "get_workflow"),
        (Kind.TABLE, TablesService, "get_table"),
        (Kind.MCP_TOOL, IntegrationService, "get_mcp_integration"),
        (Kind.MCP_SERVER, IntegrationService, "get_mcp_integration"),
        (Kind.TOOL, RegistryActionsService, "get_action_from_index"),
    ],
)
async def test_missing_and_inaccessible_are_indistinguishable(
    service, monkeypatch, kind, cls, method
):
    get = AsyncMock(side_effect=TracecatNotFoundError("Synthetic missing resource"))
    monkeypatch.setattr(cls, method, get)
    target = ReferenceTarget(
        kind,
        "core.http_request" if kind == Kind.TOOL else str(uuid4()),
        "read" if kind == Kind.MCP_TOOL else None,
    )
    with pytest.raises(ReferenceLookupError) as missing:
        await service.resolve(target, version_id=None, limits=GraphLimits())
    get.assert_awaited_once()
    service.role = service.role.model_copy(update={"scopes": frozenset()})
    get.reset_mock()
    with pytest.raises(ReferenceLookupError) as denied:
        await service.resolve(target, version_id=None, limits=GraphLimits())
    assert str(missing.value) == str(denied.value) == "unresolved"
    get.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("padding", [0, 300_000])
@pytest.mark.parametrize(
    "source_path",
    ["SKILL.md", "references/usage.markdown", "references/usage.MARKDOWN"],
)
async def test_skill_service_reads_exact_markdown_and_compiles_derived_tool(
    service, monkeypatch, padding, source_path
):
    sid, vid = uuid4(), uuid4()
    target = ReferenceTarget(Kind.SKILL, str(sid))
    tool = ReferenceTarget(Kind.TOOL, "core.http_request")
    markdown = "x" * padding + "\n\n[Request](tracecat-ref://v1/tool/core.http_request)"
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
        file_count=2,
        total_size_bytes=len(markdown) + 3_000_000,
        name="Example",
        created_at=now,
        updated_at=now,
        files=[
            SkillFileEntry(
                path=source_path,
                blob_id=uuid4(),
                sha256=digest,
                size_bytes=len(markdown),
                content_type="text/markdown",
            )
        ],
    )
    published.files.append(
        SkillFileEntry(
            path="assets/report.pdf",
            blob_id=uuid4(),
            sha256="b" * 64,
            size_bytes=3_000_000,
            content_type="application/pdf",
        )
    )
    read = AsyncMock(return_value=published)
    monkeypatch.setattr(SkillService, "get_version_read", read)
    stored = SkillBlob(
        id=published.files[0].blob_id,
        key="synthetic-key",
        bucket="synthetic-bucket",
        size_bytes=len(markdown),
        sha256=digest,
    )
    materialize = AsyncMock(return_value=[(source_path, stored)])
    monkeypatch.setattr(SkillService, "get_version_file_materialization", materialize)
    ui_read = AsyncMock(side_effect=AssertionError("Compiler must not use UI reads"))
    monkeypatch.setattr(SkillService, "get_version_file", ui_read)
    payload = BytesIO(markdown.encode())

    @asynccontextmanager
    async def stream(**kwargs):
        assert kwargs["key"] == "synthetic-key"
        yield AsyncMock(read=AsyncMock(side_effect=payload.read)), len(markdown)

    monkeypatch.setattr(blob, "open_download_stream", stream)
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.tuples.return_value.all.return_value = []
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
    # Only root -> skill and skill -> tool count as edges, regardless of assets.
    compiled = await compile_references(
        admitted, authority, service, limits=GraphLimits(edges=2)
    )
    assert not compiled.diagnostics
    assert compiled.scopes[0].grants.actions == (tool.identity,)
    read.assert_awaited_once_with(skill_id=sid, version_id=vid)
    materialize.assert_awaited_once_with(vid)
    ui_read.assert_not_awaited()
    edge = next(e for e in compiled.scopes[0].edges if e.target == tool)
    assert edge.source == target
    assert edge.location is not None and edge.location.path == source_path
    with pytest.raises(ReferenceLookupError) as oversized:
        await service.resolve(
            target, version_id=vid, limits=GraphLimits(source_bytes=len(markdown) - 1)
        )
    assert oversized.value.code == Code.LIMIT_EXCEEDED
    materialize.assert_awaited_once_with(vid)
    payload.seek(0)
    # Even a one-edge budget must not reject materialization of two files.
    await service.resolve(target, version_id=vid, limits=GraphLimits(edges=1))
    payload.seek(0)
    too_many_edges = await compile_references(
        admitted, authority, service, limits=GraphLimits(edges=1)
    )
    assert too_many_edges.diagnostics[0].code == Code.LIMIT_EXCEEDED

    registry_rows = MagicMock()
    registry_rows.scalars.return_value.all.return_value = ["core.a", "core.z"]
    mcp_rows = MagicMock()
    integration_id = uuid4()
    mcp_rows.tuples.return_value.all.return_value = [
        (integration_id, "a"),
        (integration_id, "z"),
    ]
    execute = AsyncMock(side_effect=[registry_rows, mcp_rows])
    service.session.execute = execute
    payload.seek(0)
    projected = await service.resolve(target, version_id=vid, limits=GraphLimits())
    assert [d.target for d in projected.declarations] == [
        ReferenceTarget(Kind.TOOL, "core.a"),
        ReferenceTarget(Kind.TOOL, "core.z"),
        ReferenceTarget(Kind.MCP_TOOL, str(integration_id), "a"),
        ReferenceTarget(Kind.MCP_TOOL, str(integration_id), "z"),
    ]
    for call, table in zip(
        execute.await_args_list,
        ["skill_version_tool", "skill_version_mcp_tool"],
        strict=True,
    ):
        statement = call.args[0]
        sql = str(statement)
        assert f"ORDER BY {table}.tool_id" in sql
        assert f"{table}.workspace_id = :workspace_id_1" in sql
        assert f"{table}.skill_version_id = :skill_version_id_1" in sql
        assert statement.compile().params == {
            "workspace_id_1": service.workspace_id,
            "skill_version_id_1": vid,
        }


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


@pytest.mark.anyio
async def test_historical_child_binding_selects_head_once_then_reuses_turn_pin(
    service, monkeypatch
):
    parent_id, child_id = uuid4(), uuid4()
    old_child_id, selected_child_id, newer_child_id = uuid4(), uuid4(), uuid4()
    child_target = ReferenceTarget(Kind.AGENT, str(child_id))
    parent = AgentPresetVersion(
        id=uuid4(),
        preset_id=parent_id,
        instructions="Parent",
        actions=[],
        namespaces=[],
        tool_approvals={},
        mcp_integrations=[],
        enable_internet_access=False,
        agents={
            "subagents": [
                {
                    "preset": "helper",
                    "preset_id": str(child_id),
                    "preset_version_id": str(old_child_id),
                    "preset_version": 1,
                }
            ]
        },
    )
    selected_child = AgentPresetVersion(
        id=selected_child_id,
        preset_id=child_id,
        instructions="Selected v2",
        actions=["core.http_request"],
        namespaces=[],
        tool_approvals={},
        mcp_integrations=[],
        enable_internet_access=False,
        agents={"subagents": []},
    )
    newer_child = AgentPresetVersion(
        id=newer_child_id,
        preset_id=child_id,
        instructions="New v3",
        actions=[],
        namespaces=[],
        tool_approvals={},
        mcp_integrations=[],
        enable_internet_access=False,
        agents={"subagents": []},
    )
    current_child = selected_child

    async def resolve_version(*, preset_id, preset_version_id=None):
        if preset_id == parent_id:
            return parent
        assert preset_id == child_id
        assert preset_version_id != old_child_id
        if preset_version_id == selected_child_id:
            return selected_child
        assert preset_version_id is None
        return current_child

    resolver = AsyncMock(side_effect=resolve_version)
    monkeypatch.setattr(AgentPresetService, "resolve_agent_preset_version", resolver)
    monkeypatch.setattr(
        AgentPresetService,
        "get_preset",
        AsyncMock(
            side_effect=lambda pid: AgentPreset(
                id=pid, slug="parent" if pid == parent_id else "helper"
            )
        ),
    )
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    service.session.execute = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        RegistryActionsService,
        "get_action_from_index",
        AsyncMock(return_value=object()),
    )
    loaded = await service.resolve(
        ReferenceTarget(Kind.AGENT, str(parent_id)),
        version_id=parent.id,
        limits=GraphLimits(),
    )
    call = service.session.execute.await_args
    assert call is not None
    statement = call.args[0]
    assert "ORDER BY agent_preset_version_skill.skill_id" in str(statement)
    authored = AuthoredReferenceInput(
        root=loaded.sources[0],
        explicit=ExplicitDeclarations(
            targets=tuple(d.target for d in loaded.declarations)
        ),
    )
    authority = ExecutionAuthority(
        actor_id=uuid4(),
        session_mode="delegated_preset",
        activate_references=True,
        action_scope_ceiling=None,
        namespace_ceiling=None,
        admission_policy_hash="a" * 64,
    )
    aliases = ((child_target, "helper"),)
    fresh = await compile_references(authored, authority, service, aliases=aliases)
    assert not fresh.diagnostics
    selected = next(s for s in fresh.scopes[1].selected if s.target == child_target)
    assert selected.version_id == selected_child_id
    assert fresh.scopes[1].grants.actions == ("core.http_request",)

    current_child = newer_child
    resumed = await compile_references(
        authored, authority, service, aliases=aliases, selected_versions=(selected,)
    )
    assert resumed == fresh
    resolver.assert_awaited_with(
        preset_id=child_id, preset_version_id=selected_child_id
    )
    another_turn = await compile_references(
        authored, authority, service, aliases=aliases
    )
    assert not another_turn.diagnostics
    assert another_turn.scopes[1].grants.actions == ()
    assert (
        next(
            s for s in another_turn.scopes[1].selected if s.target == child_target
        ).version_id
        == newer_child_id
    )
    incomplete = await compile_references(
        authored, authority, service, aliases=aliases, selected_versions=()
    )
    assert not incomplete.scopes
    assert incomplete.diagnostics[0].code == Code.SNAPSHOT_CONFLICT


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload,declared,digest,budget,code",
    [
        (b"\xff", 1, hashlib.sha256(b"\xff").hexdigest(), 10, Code.INVALID_SOURCE),
        (b"text", 4, "a" * 64, 10, Code.INVALID_SOURCE),
        (b"too long", 3, "a" * 64, 10, Code.INVALID_SOURCE),
        (b"short", 6, "a" * 64, 10, Code.INVALID_SOURCE),
        (b"too long", 3, "a" * 64, 3, Code.LIMIT_EXCEEDED),
        (b"text", 4, "a" * 64, 3, Code.LIMIT_EXCEEDED),
    ],
)
async def test_markdown_stream_validates_bytes_before_compilation(
    monkeypatch, payload, declared, digest, budget, code
):
    buffer = BytesIO(payload)

    @asynccontextmanager
    async def stream(**kwargs):
        # Unknown Content-Length and short chunks exercise the streaming bound.
        yield (
            AsyncMock(
                read=AsyncMock(side_effect=lambda amount: buffer.read(min(amount, 2)))
            ),
            None,
        )

    monkeypatch.setattr(blob, "open_download_stream", stream)
    stored = SkillBlob(
        key="synthetic-key",
        bucket="synthetic-bucket",
        size_bytes=declared,
        sha256=digest,
    )
    with pytest.raises(ReferenceLookupError) as exc:
        await ReferenceService._read_markdown(stored, budget)
    assert exc.value.code == code


@pytest.mark.anyio
async def test_malformed_stored_agent_config_is_not_an_authored_diagnostic(
    service, monkeypatch
):
    pid = uuid4()
    version = AgentPresetVersion(
        id=uuid4(), preset_id=pid, instructions="", agents={"subagents": "corrupt"}
    )
    monkeypatch.setattr(
        AgentPresetService,
        "resolve_agent_preset_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr(
        AgentPresetService,
        "get_preset",
        AsyncMock(return_value=AgentPreset(id=pid, slug="example")),
    )
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    service.session.execute = AsyncMock(return_value=rows)
    with pytest.raises(ValidationError):
        await service.resolve(
            ReferenceTarget(Kind.AGENT, str(pid)),
            version_id=version.id,
            limits=GraphLimits(),
        )


@pytest.mark.anyio
async def test_stdio_unknown_catalog_is_not_a_zero_tool_grant(service, monkeypatch):
    row = integration("stdio")
    row.tools = None
    monkeypatch.setattr(
        IntegrationService, "get_mcp_integration", AsyncMock(return_value=row)
    )
    with pytest.raises(ReferenceLookupError) as exc:
        await service.policy(
            (ReferenceTarget(Kind.MCP_SERVER, str(row.id)),), ScopePolicy()
        )
    assert exc.value.code == Code.NOT_READY


@pytest.mark.anyio
async def test_stdio_counts_entire_catalog_including_disabled_tools(
    service, monkeypatch
):
    row = integration("stdio")
    monkeypatch.setattr(
        IntegrationService, "get_mcp_integration", AsyncMock(return_value=row)
    )
    grants = await service.policy(
        (ReferenceTarget(Kind.MCP_SERVER, str(row.id)),), ScopePolicy()
    )
    assert grants.tool_count == 2


@pytest.mark.anyio
async def test_archived_skill_remains_unavailable_even_with_version_pin(
    service, monkeypatch
):
    get = AsyncMock(return_value=None)
    monkeypatch.setattr(SkillService, "get_skill", get)
    read = AsyncMock(side_effect=AssertionError("Must not bypass deletion"))
    monkeypatch.setattr(SkillService, "get_version_read", read)
    sid = uuid4()
    with pytest.raises(ReferenceLookupError) as exc:
        await service.resolve(
            ReferenceTarget(Kind.SKILL, str(sid)),
            version_id=uuid4(),
            limits=GraphLimits(),
        )
    assert exc.value.code == Code.UNRESOLVED
    get.assert_awaited_once_with(sid)
    read.assert_not_awaited()


@pytest.mark.anyio
async def test_configured_tool_limit_counts_expanded_mcp_tools(service, monkeypatch):
    row = integration()
    monkeypatch.setattr(
        IntegrationService, "get_mcp_integration", AsyncMock(return_value=row)
    )
    monkeypatch.setattr(config, "TRACECAT__AGENT_MAX_TOOLS", 1)
    targets = (
        ReferenceTarget(Kind.TOOL, "core.http_request"),
        ReferenceTarget(Kind.MCP_SERVER, str(row.id)),
    )
    with pytest.raises(ReferenceLookupError) as exc:
        await service.policy(targets, ScopePolicy())
    assert exc.value.code == Code.LIMIT_EXCEEDED


@pytest.mark.anyio
@pytest.mark.parametrize("during_read", [False, True])
async def test_storage_outages_remain_operational_errors(monkeypatch, during_read):
    error = blob.StorageDownloadError(error_code="SlowDown")

    @asynccontextmanager
    async def stream(**kwargs):
        if not during_read:
            raise error
        yield AsyncMock(read=AsyncMock(side_effect=error)), None

    monkeypatch.setattr(blob, "open_download_stream", stream)
    stored = SkillBlob(
        key="synthetic-key", bucket="synthetic-bucket", size_bytes=4, sha256="a" * 64
    )
    with pytest.raises(blob.StorageDownloadError) as exc:
        await ReferenceService._read_markdown(stored, 10)
    assert exc.value is error
