from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tracecat.agent.preset import router as agent_preset_router
from tracecat.auth.types import Role
from tracecat.cases.dropdowns import router as case_dropdowns_router
from tracecat.cases.durations import router as case_durations_router
from tracecat.cases.tag_definitions import router as case_tag_definitions_router
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError
from tracecat.inbox import router as inbox_router
from tracecat.integrations import router as integrations_router
from tracecat.organization import router as organization_router
from tracecat.registry.actions import router as registry_actions_router
from tracecat.registry.repositories import router as registry_repos_router
from tracecat.tables import router as tables_router
from tracecat.vcs import router as vcs_router
from tracecat.workflow.executions import router as workflow_executions_router
from tracecat.workflow.graph import router as workflow_graph_router
from tracecat.workflow.store import router as workflow_store_router

type AsyncEndpoint = Callable[..., Awaitable[object]]


async def _assert_endpoint_requires_scope(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    no_scope_role = Role(
        type="user",
        service_id="tracecat-api",
        scopes=frozenset(),
    )
    token = ctx_role.set(no_scope_role)
    try:
        with pytest.raises(ScopeDeniedError):
            await endpoint()
    finally:
        ctx_role.reset(token)

    allowed_role = Role(
        type="user",
        service_id="tracecat-api",
        scopes=frozenset({required_scope}),
    )
    token = ctx_role.set(allowed_role)
    try:
        with pytest.raises(TypeError):
            await endpoint()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (registry_repos_router.list_repository_versions, "org:registry:read"),
        (registry_repos_router.list_registry_repositories, "org:registry:read"),
        (registry_repos_router.get_registry_repository, "org:registry:read"),
        (registry_repos_router.list_repository_commits, "org:registry:read"),
        (registry_repos_router.compare_registry_versions, "org:registry:read"),
        (registry_repos_router.get_previous_registry_version, "org:registry:read"),
        (registry_repos_router.sync_registry_repository, "org:registry:update"),
        (registry_repos_router.create_registry_repository, "org:registry:create"),
        (registry_repos_router.update_registry_repository, "org:registry:update"),
        (registry_repos_router.delete_registry_repository, "org:registry:delete"),
        (registry_repos_router.promote_registry_version, "org:registry:update"),
        (registry_repos_router.delete_registry_version, "org:registry:delete"),
    ],
)
async def test_registry_repository_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (registry_actions_router.list_registry_actions, "org:registry:read"),
        (registry_actions_router.get_registry_action, "org:registry:read"),
    ],
)
async def test_registry_action_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (agent_preset_router.list_agent_presets, "agent:read"),
        (agent_preset_router.get_agent_preset, "agent:read"),
        (agent_preset_router.get_agent_preset_by_slug, "agent:read"),
        (agent_preset_router.list_agent_preset_versions, "agent:read"),
        (agent_preset_router.get_agent_preset_version, "agent:read"),
        (agent_preset_router.compare_agent_preset_versions, "agent:read"),
        (agent_preset_router.create_agent_preset, "agent:create"),
        (agent_preset_router.update_agent_preset, "agent:update"),
        (agent_preset_router.restore_agent_preset_version, "agent:update"),
        (agent_preset_router.delete_agent_preset, "agent:delete"),
    ],
)
async def test_agent_preset_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
async def test_table_update_row_requires_table_update_scope() -> None:
    await _assert_endpoint_requires_scope(tables_router.update_row, "table:update")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (inbox_router.list_items, "inbox:read"),
    ],
)
async def test_inbox_scope_guards(endpoint: AsyncEndpoint, required_scope: str) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (organization_router.get_organization_entitlements, "org:read"),
        (organization_router.revoke_invitation, "org:member:invite"),
    ],
)
async def test_organization_invitation_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (workflow_store_router.publish_workflow, "workflow:update"),
        (workflow_store_router.list_workflow_commits, "workflow:read"),
        (workflow_store_router.pull_workflows, "workflow:update"),
    ],
)
async def test_workflow_store_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (workflow_executions_router.cancel_workflow_execution, "workflow:terminate"),
        (workflow_executions_router.terminate_workflow_execution, "workflow:terminate"),
    ],
)
async def test_workflow_execution_stop_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (workflow_graph_router.get_graph, "workflow:read"),
        (workflow_graph_router.apply_graph_operations, "workflow:update"),
    ],
)
async def test_workflow_graph_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (case_dropdowns_router.list_dropdown_definitions, "case:read"),
        (case_dropdowns_router.create_dropdown_definition, "case:create"),
        (case_dropdowns_router.get_dropdown_definition, "case:read"),
        (case_dropdowns_router.update_dropdown_definition, "case:update"),
        (case_dropdowns_router.delete_dropdown_definition, "case:delete"),
        (case_dropdowns_router.add_dropdown_option, "case:create"),
        (case_dropdowns_router.update_dropdown_option, "case:update"),
        (case_dropdowns_router.delete_dropdown_option, "case:delete"),
        (case_dropdowns_router.reorder_dropdown_options, "case:update"),
        (case_dropdowns_router.list_case_dropdown_values, "case:read"),
        (case_dropdowns_router.set_case_dropdown_value, "case:update"),
        (case_durations_router.list_case_duration_definitions, "case:read"),
        (case_durations_router.get_case_duration_definition, "case:read"),
        (case_durations_router.create_case_duration_definition, "case:create"),
        (case_durations_router.update_case_duration_definition, "case:update"),
        (case_durations_router.delete_case_duration_definition, "case:delete"),
        (case_durations_router.list_case_durations, "case:read"),
        (case_durations_router.get_case_duration, "case:read"),
        (case_durations_router.create_case_duration, "case:create"),
        (case_durations_router.update_case_duration, "case:update"),
        (case_durations_router.delete_case_duration, "case:delete"),
        (case_tag_definitions_router.list_case_tags, "case:read"),
        (case_tag_definitions_router.get_case_tag, "case:read"),
        (case_tag_definitions_router.create_case_tag, "case:create"),
        (case_tag_definitions_router.update_case_tag, "case:update"),
        (case_tag_definitions_router.delete_case_tag, "case:delete"),
    ],
)
async def test_case_scope_guards(endpoint: AsyncEndpoint, required_scope: str) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (integrations_router.list_integrations, "integration:read"),
        (integrations_router.get_integration, "integration:read"),
        (integrations_router.connect_provider, "integration:update"),
        (integrations_router.disconnect_integration, "integration:update"),
        (integrations_router.delete_integration, "integration:delete"),
        (integrations_router.test_connection, "integration:update"),
        (integrations_router.update_integration, "integration:update"),
        (integrations_router.create_custom_provider, "integration:create"),
        (integrations_router.list_providers, "integration:read"),
        (integrations_router.get_provider, "integration:read"),
        (integrations_router.create_mcp_integration, "integration:create"),
        (integrations_router.list_mcp_integrations, "integration:read"),
        (integrations_router.get_mcp_integration, "integration:read"),
        (integrations_router.update_mcp_integration, "integration:update"),
        (integrations_router.delete_mcp_integration, "integration:delete"),
    ],
)
async def test_integration_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (vcs_router.github_app_install_callback, "org:settings:update"),
        (vcs_router.save_github_app_credentials, "org:settings:update"),
        (vcs_router.delete_github_app_credentials, "org:settings:delete"),
        (vcs_router.get_github_app_credentials_status, "org:settings:read"),
    ],
)
async def test_vcs_scope_guards(endpoint: AsyncEndpoint, required_scope: str) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)
