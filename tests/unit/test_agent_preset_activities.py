from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

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
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


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


def test_merge_resolved_refs_runtime_pass_wins_per_node() -> None:
    """Invariant: one entry per node identity; the later pass is authoritative.

    On a resumed session the root pass resolves a subagent's current head
    while the runtime pass restores the stored binding
    verbatim — the merged snapshot must carry exactly one entry for that
    node, the runtime one.
    """

    subagent_id = uuid.uuid4()
    root_version = uuid.uuid4()
    restored_version = uuid.uuid4()
    root_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=subagent_id,
                resolved_version_id=root_version,
                status="ok",
            )
        ]
    )
    runtime_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=subagent_id,
                resolved_version_id=restored_version,
                status="ok",
            )
        ]
    )

    merged = merge_resolved_refs(root_pass, runtime_pass)

    assert merged is not None
    assert len(merged.refs) == 1
    assert merged.refs[0].resolved_version_id == restored_version


def test_merge_resolved_refs_earlier_only_nodes_survive() -> None:
    """Invariant: a root-pass skip record with no runtime counterpart survives.

    A subagent skipped at root resolution never reaches the runtime binding,
    so its skip record exists only in the earlier pass and must not be
    dropped by the merge. Slug-keyed identities (no resource_id) count too,
    and first-seen order is preserved.
    """

    skipped_id = uuid.uuid4()
    resolved_id = uuid.uuid4()
    root_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=skipped_id,
                status="skipped",
                code="deleted",
                successor_id=uuid.uuid4(),
            ),
            ResolvedRef(
                resource_kind="subagent",
                slug="slug-only-skip",
                status="skipped",
                code="not_found",
            ),
        ]
    )
    runtime_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=resolved_id,
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )

    merged = merge_resolved_refs(root_pass, runtime_pass)

    assert merged is not None
    assert [(ref.resource_id or ref.slug, ref.status) for ref in merged.refs] == [
        (skipped_id, "skipped"),
        ("slug-only-skip", "skipped"),
        (resolved_id, "ok"),
    ]


def test_merge_resolved_refs_id_entry_supersedes_slug_only_provisional() -> None:
    """Invariant: a slug-only entry is provisional for the same kind+slug.

    A slug-only skip (unresolved slug-ref) followed by an id-bearing entry
    carrying the same slug is one node observed twice — the id-bearing pass
    supersedes in place instead of leaving a contradictory pair.
    """

    resolved_id = uuid.uuid4()
    slug_only = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                slug="shared-slug",
                status="skipped",
                code="not_found",
            )
        ]
    )
    id_bearing = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=resolved_id,
                slug="shared-slug",
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )

    merged = merge_resolved_refs(slug_only, id_bearing)

    assert merged is not None
    assert len(merged.refs) == 1
    assert merged.refs[0].resource_id == resolved_id
    assert merged.refs[0].status == "ok"


def test_merge_resolved_refs_distinct_ids_never_merge_via_slug() -> None:
    """Invariant: slug reuse never conflates distinct resources.

    A live successor that reused a deleted node's slug is a different
    resource — its ok entry must not replace the deleted node's skip record
    (or erase its successor_id annotation).
    """

    deleted_id = uuid.uuid4()
    successor_id = uuid.uuid4()
    earlier = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=deleted_id,
                slug="reused-slug",
                status="skipped",
                code="deleted",
                successor_id=successor_id,
            )
        ]
    )
    later = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=successor_id,
                slug="reused-slug",
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )

    merged = merge_resolved_refs(earlier, later)

    assert merged is not None
    assert [(ref.resource_id, ref.status) for ref in merged.refs] == [
        (deleted_id, "skipped"),
        (successor_id, "ok"),
    ]
    assert merged.refs[0].successor_id == successor_id


def test_merge_resolved_refs_preserves_root_skill_across_passes() -> None:
    """Invariant: skill entries from different tree positions both survive.

    The root preset and a subagent can share a skill. The root activity
    freezes the version the root agent actually runs with; a later subagent
    activity may resolve the same skill after its head advances. Those
    are two true observations of different tree positions — the child's
    entry must not clobber the root's, while the subagent node itself is
    still replaced by the runtime pass.
    """

    skill_id = uuid.uuid4()
    root_skill_version = uuid.uuid4()
    child_skill_version = uuid.uuid4()
    subagent_id = uuid.uuid4()
    preset_id = uuid.uuid4()

    root_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                resource_id=preset_id,
                resolved_version_id=uuid.uuid4(),
                status="ok",
            ),
            ResolvedRef(
                resource_kind="skill",
                resource_id=skill_id,
                resolved_version_id=root_skill_version,
                status="ok",
            ),
            ResolvedRef(
                resource_kind="subagent",
                resource_id=subagent_id,
                resolved_version_id=uuid.uuid4(),
                status="ok",
            ),
        ]
    )
    runtime_subagent_version = uuid.uuid4()
    runtime_pass = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="subagent",
                resource_id=subagent_id,
                resolved_version_id=runtime_subagent_version,
                status="ok",
            ),
            ResolvedRef(
                resource_kind="skill",
                resource_id=skill_id,
                resolved_version_id=child_skill_version,
                status="ok",
            ),
        ]
    )

    merged = merge_resolved_refs(root_pass, runtime_pass)

    assert merged is not None
    assert [(ref.resource_kind, ref.resolved_version_id) for ref in merged.refs] == [
        ("preset", root_pass.refs[0].resolved_version_id),
        ("skill", root_skill_version),
        ("subagent", runtime_subagent_version),
        ("skill", child_skill_version),
    ]


def test_merge_resolved_refs_identical_entries_collapse() -> None:
    """Invariant: duplicate entries for the same node collapse to one."""

    ref = ResolvedRef(
        resource_kind="skill",
        resource_id=uuid.uuid4(),
        resolved_version_id=uuid.uuid4(),
        status="ok",
    )

    merged = merge_resolved_refs(
        ResolvedRefs(refs=[ref]), ResolvedRefs(refs=[ref.model_copy()])
    )

    assert merged is not None
    assert merged.refs == [ref]


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
    db_session = SimpleNamespace(add=MagicMock(), commit=AsyncMock())
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
    assert result["subagents"][0]["preset_version_id"] == str(preset_version_id)
    assert result["subagents"][0]["preset_version"] == 8


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
