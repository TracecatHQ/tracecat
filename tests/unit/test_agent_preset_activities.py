from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.agent.preset.activities import (
    ResolveAgentPresetVersionRefActivityInput,
    ResolveAgentsConfigActivityInput,
    resolve_agent_preset_version_ref_activity,
    resolve_agents_config_activity,
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.preset.resolved_refs import (
    ResolvedRef,
    ResolvedRefs,
    merge_resolved_refs,
    without_subagent_refs,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.mark.anyio
async def test_resolve_agent_preset_version_ref_activity_ignores_legacy_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(id=uuid.uuid4(), preset_id=uuid.uuid4())
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agent_preset_version_ref_activity(
        ResolveAgentPresetVersionRefActivityInput.model_validate(
            {
                "role": role,
                "preset_slug": "triage-agent",
                "preset_version": 3,
            }
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(slug="triage-agent")
    assert result.preset_id == version.preset_id
    assert result.preset_version_id == version.id


@pytest.mark.anyio
async def test_preflight_records_failure_refs_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DSL preflight failures still leave classified turn provenance."""

    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    failure_refs = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                slug="missing-preset",
                status="skipped",
                code="not_found",
            )
        ]
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    no_rows = SimpleNamespace(scalar_one_or_none=MagicMock(return_value=None))
    db_session = SimpleNamespace(
        execute=AsyncMock(return_value=no_rows),
        add=MagicMock(),
        commit=AsyncMock(),
    )
    service = SimpleNamespace(
        role=role,
        session=db_session,
        resolve_agent_preset_version=AsyncMock(
            side_effect=TracecatNotFoundError("Preset not found")
        ),
        build_root_preset_failure_refs=AsyncMock(return_value=failure_refs),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(TracecatNotFoundError):
        await resolve_agent_preset_version_ref_activity(
            ResolveAgentPresetVersionRefActivityInput(
                role=role,
                preset_slug="missing-preset",
                session_id=session_id,
                wf_exec_id="wf-dsl-exec-1",
            )
        )

    service.build_root_preset_failure_refs.assert_awaited_once_with(
        slug="missing-preset",
    )
    provenance = db_session.add.call_args.args[0]
    assert provenance.session_id == session_id
    assert provenance.wf_exec_id == "wf-dsl-exec-1"
    assert provenance.resolved_refs == failure_refs.model_dump(mode="json")
    db_session.commit.assert_awaited_once_with()


def test_resolve_agents_config_input_defaults_preserve_resolved_versions() -> None:
    """Workflow histories recorded before the flag existed must deserialize,
    defaulting to fresh current-head resolution."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    payload = ResolveAgentsConfigActivityInput(role=role).model_dump(mode="json")
    payload.pop("preserve_resolved_versions")
    parsed = ResolveAgentsConfigActivityInput.model_validate(payload)
    assert parsed.preserve_resolved_versions is False


def test_agent_workflow_args_default_dispatch_resolution_fields() -> None:
    """Invariant: pre-2.3a workflow payloads deserialize and use legacy routing."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    args = AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            user_prompt="hello",
            session_id=uuid.uuid4(),
            config=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
            ),
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )
    payload = args.model_dump(mode="json")
    payload.pop("resolved_agent_config")
    payload["agent_args"].pop("resolved_agents_config")

    parsed = AgentWorkflowArgs.model_validate(payload)

    assert parsed.resolved_agent_config is None
    assert parsed.agent_args.resolved_agents_config is None


def test_resolve_agents_config_result_derives_session_binding() -> None:
    binding = ResolvedAttachedSubagentRef(
        preset="analyst",
        preset_version=3,
        name=None,
        description=None,
        max_turns=5,
        preset_id=uuid.uuid4(),
        preset_version_id=uuid.uuid4(),
    )
    result = ResolvedAgentsRuntimeConfig(
        enabled=True,
        subagents=[
            ResolvedSubagentConfig(
                binding=binding,
                description="Runtime fallback description",
                prompt="Subagent prompt",
                config=AgentConfigPayload(
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    retries=3,
                ),
            )
        ],
    )

    assert result.subagents[0].alias == "analyst"
    assert result.subagents[0].max_turns == 5
    agents_binding = result.to_agents_binding()
    assert agents_binding.enabled is True
    assert agents_binding.subagents == [binding]


def test_without_subagent_refs_keeps_non_subagent_entries() -> None:
    """Preserved-binding turns retain root refs but not fresh child refs."""

    preset_id = uuid.uuid4()
    refs = ResolvedRefs(
        refs=[
            ResolvedRef(resource_kind="preset", resource_id=preset_id, status="ok"),
            ResolvedRef(resource_kind="skill", resource_id=uuid.uuid4(), status="ok"),
            ResolvedRef(
                resource_kind="subagent", resource_id=uuid.uuid4(), status="ok"
            ),
            ResolvedRef(
                resource_kind="subagent",
                slug="gone-child",
                status="skipped",
                code="deleted",
            ),
        ]
    )

    filtered = without_subagent_refs(refs)

    assert filtered is not None
    assert [ref.resource_kind for ref in filtered.refs] == ["preset", "skill"]
    assert filtered.refs[0].resource_id == preset_id
    assert without_subagent_refs(None) is None


type _RefSpec = tuple[
    str,
    int | None,
    str | None,
    int | None,
    str,
    str | None,
    int | None,
]


def _ref_spec(
    kind: str,
    resource_id: int | None = None,
    slug: str | None = None,
    version_id: int | None = None,
    status: str = "ok",
    code: str | None = None,
    successor_id: int | None = None,
) -> _RefSpec:
    return kind, resource_id, slug, version_id, status, code, successor_id


def _resolved_ref(spec: _RefSpec) -> ResolvedRef:
    kind, resource_id, slug, version_id, status, code, successor_id = spec
    return ResolvedRef.model_validate(
        {
            "resource_kind": kind,
            "resource_id": uuid.UUID(int=resource_id) if resource_id else None,
            "slug": slug,
            "resolved_version_id": uuid.UUID(int=version_id) if version_id else None,
            "status": status,
            "code": code,
            "successor_id": uuid.UUID(int=successor_id) if successor_id else None,
        }
    )


def _resolved_ref_spec(ref: ResolvedRef) -> _RefSpec:
    return _ref_spec(
        ref.resource_kind,
        ref.resource_id.int if ref.resource_id else None,
        ref.slug,
        ref.resolved_version_id.int if ref.resolved_version_id else None,
        ref.status,
        ref.code,
        ref.successor_id.int if ref.successor_id else None,
    )


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        pytest.param(
            [_ref_spec("subagent", 1, version_id=2)],
            [_ref_spec("subagent", 1, version_id=3)],
            [_ref_spec("subagent", 1, version_id=3)],
            id="later-pass-wins",
        ),
        pytest.param(
            [
                _ref_spec(
                    "subagent", 4, status="skipped", code="deleted", successor_id=5
                ),
                _ref_spec(
                    "subagent",
                    slug="slug-only-skip",
                    status="skipped",
                    code="not_found",
                ),
            ],
            [_ref_spec("subagent", 6, version_id=7)],
            [
                _ref_spec(
                    "subagent", 4, status="skipped", code="deleted", successor_id=5
                ),
                _ref_spec(
                    "subagent",
                    slug="slug-only-skip",
                    status="skipped",
                    code="not_found",
                ),
                _ref_spec("subagent", 6, version_id=7),
            ],
            id="earlier-only-survives",
        ),
        pytest.param(
            [
                _ref_spec(
                    "subagent",
                    slug="shared-slug",
                    status="skipped",
                    code="not_found",
                )
            ],
            [_ref_spec("subagent", 8, "shared-slug", 9)],
            [_ref_spec("subagent", 8, "shared-slug", 9)],
            id="id-supersedes-provisional-slug",
        ),
        pytest.param(
            [
                _ref_spec(
                    "subagent",
                    10,
                    "reused-slug",
                    status="skipped",
                    code="deleted",
                    successor_id=11,
                )
            ],
            [_ref_spec("subagent", 11, "reused-slug", 12)],
            [
                _ref_spec(
                    "subagent",
                    10,
                    "reused-slug",
                    status="skipped",
                    code="deleted",
                    successor_id=11,
                ),
                _ref_spec("subagent", 11, "reused-slug", 12),
            ],
            id="distinct-ids-survive-slug-reuse",
        ),
        pytest.param(
            [
                _ref_spec("preset", 13, version_id=14),
                _ref_spec("skill", 15, version_id=16),
                _ref_spec("subagent", 17, version_id=18),
            ],
            [
                _ref_spec("subagent", 17, version_id=19),
                _ref_spec("skill", 15, version_id=20),
            ],
            [
                _ref_spec("preset", 13, version_id=14),
                _ref_spec("skill", 15, version_id=16),
                _ref_spec("subagent", 17, version_id=19),
                _ref_spec("skill", 15, version_id=20),
            ],
            id="tree-position-skills-survive",
        ),
        pytest.param(
            [_ref_spec("skill", 21, version_id=22)],
            [_ref_spec("skill", 21, version_id=22)],
            [_ref_spec("skill", 21, version_id=22)],
            id="identical-collapses",
        ),
    ],
)
def test_merge_resolved_refs_cases(
    earlier: list[_RefSpec],
    later: list[_RefSpec],
    expected: list[_RefSpec],
) -> None:
    """Merge by stable node identity while preserving true observations."""

    merged = merge_resolved_refs(
        ResolvedRefs(refs=[_resolved_ref(spec) for spec in earlier]),
        ResolvedRefs(refs=[_resolved_ref(spec) for spec in later]),
    )

    assert merged is not None
    assert [_resolved_ref_spec(ref) for ref in merged.refs] == expected


def test_resolution_outputs_parse_pre_2_2_payloads_without_resolved_refs() -> None:
    """Invariant: pre-2.2 activity histories deserialize without new fields."""

    runtime_config = ResolvedAgentsRuntimeConfig.model_validate(
        {"enabled": False, "subagents": []}
    )
    payload = AgentConfigPayload.model_validate(
        {
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "retries": 3,
        }
    )

    assert runtime_config.resolved_refs is None
    assert payload.resolved_refs is None


@pytest.mark.anyio
async def test_merged_provenance_appends_over_existing_root_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy fallback turns append merged refs over the root-only row."""

    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    root_refs = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                resource_id=uuid.uuid4(),
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    # The root activity already wrote a row with a different snapshot.
    stale_row = SimpleNamespace(scalar_one_or_none=MagicMock(return_value={"refs": []}))
    db_session = SimpleNamespace(
        execute=AsyncMock(return_value=stale_row),
        add=MagicMock(),
        commit=AsyncMock(),
    )
    service = SimpleNamespace(role=role, session=db_session)
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(),
            session_id=session_id,
            wf_exec_id="turn-1",
            parent_resolved_refs=root_refs,
        )
    )

    assert result.resolved_refs == root_refs
    provenance = db_session.add.call_args.args[0]
    assert provenance.resolved_refs == root_refs.model_dump(mode="json")
    db_session.commit.assert_awaited_once_with()


@pytest.mark.anyio
async def test_merged_provenance_skips_identical_latest_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch-staged turns do not duplicate an identical snapshot."""

    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    root_refs = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                resource_id=uuid.uuid4(),
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    identical_row = SimpleNamespace(
        scalar_one_or_none=MagicMock(return_value=root_refs.model_dump(mode="json"))
    )
    db_session = SimpleNamespace(
        execute=AsyncMock(return_value=identical_row),
        add=MagicMock(),
        commit=AsyncMock(),
    )
    service = SimpleNamespace(role=role, session=db_session)
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(),
            session_id=session_id,
            wf_exec_id="turn-1",
            parent_resolved_refs=root_refs,
        )
    )

    assert result.resolved_refs == root_refs
    db_session.add.assert_not_called()
    db_session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_disabled_agents_activity_persists_parent_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    root_refs = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                resource_id=uuid.uuid4(),
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    existing_result = SimpleNamespace(scalar_one_or_none=MagicMock(return_value=None))
    db_session = SimpleNamespace(
        execute=AsyncMock(return_value=existing_result),
        add=MagicMock(),
        commit=AsyncMock(),
    )
    service = SimpleNamespace(role=role, session=db_session)

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(),
            session_id=session_id,
            wf_exec_id="turn-1",
            parent_resolved_refs=root_refs,
        )
    )

    assert result.enabled is False
    assert result.resolved_refs == root_refs
    provenance = db_session.add.call_args.args[0]
    assert provenance.workspace_id == workspace_id
    assert provenance.session_id == session_id
    assert provenance.wf_exec_id == "turn-1"
    assert provenance.resolved_refs == root_refs.model_dump(mode="json")
    db_session.execute.assert_awaited_once()
    db_session.commit.assert_awaited_once_with()


@pytest.mark.anyio
async def test_resolve_preset_subagent_configs_uses_preset_id_ref() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = AgentPresetService(cast(Any, SimpleNamespace()), role)
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=8,
        agents={"enabled": False},
        tool_approvals={},
    )
    service.resolve_agent_preset_version = AsyncMock(return_value=version)
    service.resolve_agent_preset_version_for_subagent_ref = AsyncMock(
        return_value=version
    )
    service._lock_active_subagent_presets = AsyncMock()  # type: ignore[method-assign]
    # The edge-authoritative ban check hits the DB; stub it for the double.
    service._get_version_agents_config = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentSubagentsConfig(enabled=False)
    )
    service.get_preset = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(slug="old-analyst-slug", description=None)
    )

    result = await service._resolve_preset_subagent_configs(
        AgentSubagentsConfig(
            enabled=True,
            subagents=[
                ResolvedAttachedSubagentRef(
                    preset="old-analyst-slug",
                    preset_version=2,
                    name="analyst",
                    description=None,
                    max_turns=3,
                    preset_id=preset_id,
                    preset_version_id=preset_version_id,
                )
            ],
        ),
        parent_preset_id=uuid.uuid4(),
        parent_slug="parent",
    )

    service.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        preset_id=preset_id,
    )
    service.resolve_agent_preset_version.assert_not_awaited()
    assert result.subagents[0].preset_version_id == preset_version_id
    assert result.subagents[0].preset_version == 8


@pytest.mark.anyio
async def test_resolve_agents_config_resolves_persisted_ref_by_preset_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=4,
        agents={"enabled": False},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(return_value=version),
        _get_version_agents_config=AsyncMock(
            return_value=AgentSubagentsConfig(enabled=False)
        ),
        get_preset=AsyncMock(
            return_value=SimpleNamespace(
                slug="child-preset", description="Child preset"
            )
        ),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(
                enabled=True,
                subagents=[
                    ResolvedAttachedSubagentRef(
                        preset="old-analyst-slug",
                        preset_version=2,
                        name="analyst",
                        description=None,
                        max_turns=None,
                        preset_id=preset_id,
                        preset_version_id=preset_version_id,
                    )
                ],
            ),
        )
    )

    service.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        preset_id=preset_id,
    )
    service.resolve_agent_preset_version.assert_not_awaited()
    assert result.subagents[0].binding.preset_version_id == preset_version_id
    assert result.subagents[0].binding.preset_version == 4


@pytest.mark.anyio
async def test_resolve_agents_config_ignores_legacy_follow_latest_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=4,
        agents={"enabled": False},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(return_value=version),
        _get_version_agents_config=AsyncMock(
            return_value=AgentSubagentsConfig(enabled=False)
        ),
        get_preset=AsyncMock(
            return_value=SimpleNamespace(
                slug="child-preset", description="Child preset"
            )
        ),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(
                enabled=True,
                subagents=[
                    ResolvedAttachedSubagentRef(
                        preset="old-analyst-slug",
                        preset_version=2,
                        name="analyst",
                        preset_id=preset_id,
                        preset_version_id=preset_version_id,
                    )
                ],
            ),
            follow_latest_versions=False,
        )
    )

    service.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        preset_id=preset_id,
    )
    service.resolve_agent_preset_version.assert_not_awaited()
    assert result.subagents[0].binding.preset_version_id == preset_version_id


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_subagent_with_tool_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        version=1,
        agents={"enabled": False},
        tool_approvals={"core.http_request": True},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(return_value=version),
        _get_version_agents_config=AsyncMock(
            return_value=AgentSubagentsConfig(enabled=False)
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(
        TracecatValidationError,
        match=(
            "Subagent preset 'approval-child' uses manual approvals, "
            "which are not supported for subagents yet."
        ),
    ):
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "approval-child"}],
                    }
                ),
            )
        )


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_invalid_fallback_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(SimpleNamespace()),
    )

    with pytest.raises(
        TracecatValidationError,
        match="Invalid subagent alias 'Bad Alias'",
    ):
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "Bad Alias"}],
                    }
                ),
            )
        )


@pytest.mark.anyio
async def test_resolve_custom_model_provider_config_activity_returns_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        get_workspace_provider_credentials=AsyncMock(
            return_value={
                "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://customer.example",
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/custom-model",
                "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
            }
        )
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda *_args, **_kwargs: _AsyncContext(service),
    )

    result = await resolve_custom_model_provider_config_activity(role)

    service.get_workspace_provider_credentials.assert_awaited_once_with(
        "custom-model-provider",
    )
    assert result.base_url == "https://customer.example"
    assert result.model_name == "provider/custom-model"
    assert result.passthrough is True
