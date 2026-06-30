"""Shared workflow authoring context logic.

Holds the reusable action authoring-context builder, its helpers, and the
Pydantic response schemas. Extracted from ``tracecat.mcp.server`` so non-MCP
API endpoints (e.g. the internal workflows router that backs the
``core.workflow.get_authoring_context`` registry action) can reuse the same
logic without importing the FastMCP runtime. ``tracecat.mcp.server`` re-imports
these symbols, so behavior stays identical across both surfaces.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel, Field
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.tools import create_tool_from_registry
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.service import SecretsService
from tracecat.variables.service import VariablesService


class ActionSecretRequirementPayload(TypedDict):
    """Workspace secret requirement needed by an action."""

    type: Literal["secret"]
    name: str
    required_keys: list[str]
    optional_keys: list[str]
    optional: bool


class ActionOAuthRequirementPayload(TypedDict):
    """OAuth integration requirement needed by an action.

    OAuth requirements are backed by workspace integrations (configured under
    /integrations), not by workspace secrets. ``name`` is the synthetic secret
    name (e.g. ``github_oauth``) used in ``${{ SECRETS.<name>.<token> }}``
    expressions at runtime, but readiness is evaluated against the workspace's
    configured OAuth integrations keyed by ``(provider_id, grant_type)``.
    """

    type: Literal["oauth"]
    name: str
    provider_id: str
    grant_type: str
    optional: bool


ActionRequirementPayload = (
    ActionSecretRequirementPayload | ActionOAuthRequirementPayload
)


class ActionDiscoveryResponse(BaseModel):
    """Action discovery item response."""

    action_name: str
    description: str | None = None
    configured: bool
    missing_requirements: list[str] = Field(default_factory=list)
    optional_secrets: list[str] = Field(default_factory=list)


class ActionContextResponse(ActionDiscoveryResponse):
    """Full action authoring context response."""

    parameters_json_schema: dict[str, Any]
    required_secrets: list[ActionRequirementPayload] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)


class EnabledModelInfo(BaseModel):
    """A model enabled for the workspace, usable when configuring AI actions.

    ``catalog_id`` is the canonical model selector: pass it as the ``model``
    selection on ``ai.agent``/``ai.call`` actions or as ``catalog_id`` on agent
    presets. ``model_name``/``model_provider`` are surfaced for display and for
    the deprecated legacy selectors only.
    """

    catalog_id: str
    model_name: str
    model_provider: str


class WorkflowAuthoringContextResponse(BaseModel):
    """Workflow authoring context response."""

    actions: list[ActionContextResponse] = Field(default_factory=list)
    variable_hints: list[dict[str, Any]] = Field(default_factory=list)
    secret_hints: list[dict[str, Any]] = Field(default_factory=list)
    enabled_models: list[EnabledModelInfo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def build_example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a compact example payload from JSON schema properties."""
    example: dict[str, Any] = {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        prop = properties.get(key, {})
        prop_type = prop.get("type")
        if prop_type == "string":
            example[key] = "example"
        elif prop_type == "integer":
            example[key] = 1
        elif prop_type == "number":
            example[key] = 1.0
        elif prop_type == "boolean":
            example[key] = True
        elif prop_type == "array":
            example[key] = []
        elif prop_type == "object":
            example[key] = {}
        else:
            example[key] = "value"
    return example


def secrets_to_requirements(
    secrets: Sequence[RegistrySecret | RegistryOAuthSecret],
) -> list[ActionRequirementPayload]:
    """Convert registry secret objects to public requirement metadata.

    OAuth requirements are represented as OAuth requirements (provider_id +
    grant_type), not as workspace secret/key pairs, so readiness can be checked
    against the workspace's configured OAuth integrations.
    """
    requirements: list[ActionRequirementPayload] = []
    for secret in secrets:
        if isinstance(secret, RegistrySecret):
            requirements.append(
                {
                    "type": "secret",
                    "name": secret.name,
                    "required_keys": list(secret.keys or []),
                    "optional_keys": list(secret.optional_keys or []),
                    "optional": secret.optional,
                }
            )
        elif isinstance(secret, RegistryOAuthSecret):
            requirements.append(
                {
                    "type": "oauth",
                    "name": secret.name,
                    "provider_id": secret.provider_id,
                    "grant_type": secret.grant_type,
                    "optional": secret.optional,
                }
            )
    return requirements


def optional_secret_names(
    requirements: Sequence[ActionRequirementPayload],
) -> list[str]:
    """Names of optional secret requirements (e.g. mtls/ca_cert).

    These are credentials an action *may* use but does not require to run, so
    their absence never makes an action unconfigured.
    """
    return [
        req["name"]
        for req in requirements
        if req["type"] == "secret" and req.get("optional", False)
    ]


async def load_secret_inventory(role: Role) -> dict[str, set[str]]:
    """Load workspace secret key inventory for the default environment."""
    async with SecretsService.with_session(role=role) as svc:
        workspace_inventory: dict[str, set[str]] = {}
        workspace_secrets = await svc.list_secrets()
        for secret in workspace_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            keys = {kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)}
            workspace_inventory[secret.name] = keys
        return workspace_inventory


async def load_oauth_inventory(role: Role) -> set[ProviderKey]:
    """Load connected workspace OAuth integrations keyed by provider.

    Only integrations that have completed authentication (``CONNECTED`` status,
    i.e. an access token is stored) can have
    ``${{ SECRETS.<provider>_oauth.*_TOKEN }}`` injected at runtime. A
    configured-but-not-connected provider (client credentials saved but the
    OAuth flow not completed) yields no token at runtime, so it must not count
    as configured for action readiness. Keys are workspace-level
    ``(provider_id, grant_type)`` pairs, not per-user rows.
    """
    async with IntegrationService.with_session(role=role) as svc:
        integrations = await svc.list_integrations()
    return {
        ProviderKey(id=integration.provider_id, grant_type=integration.grant_type)
        for integration in integrations
        if integration.status == IntegrationStatus.CONNECTED
    }


def evaluate_configuration(
    requirements: Sequence[ActionRequirementPayload],
    workspace_inventory: dict[str, set[str]],
    oauth_inventory: set[ProviderKey] | None = None,
) -> tuple[bool, list[str]]:
    """Evaluate whether required secrets and OAuth integrations are configured."""
    oauth_inventory = oauth_inventory or set()
    missing: list[str] = []
    for req in requirements:
        if req.get("type") == "oauth":
            oauth_req = cast(ActionOAuthRequirementPayload, req)
            if oauth_req.get("optional", False):
                continue
            provider_key = ProviderKey(
                id=oauth_req["provider_id"],
                grant_type=OAuthGrantType(oauth_req["grant_type"]),
            )
            if provider_key not in oauth_inventory:
                missing.append(
                    "missing oauth integration: "
                    f"{provider_key.id} ({provider_key.grant_type.value})"
                )
            continue

        secret_req = cast(ActionSecretRequirementPayload, req)
        if secret_req.get("optional", False):
            # A wholly-optional secret never blocks readiness, even when it
            # declares keys (e.g. the mtls/ca_cert secrets inherited from
            # core.http_request). At runtime these are absent-by-default, so an
            # action that only "needs" optional secrets is still usable.
            continue
        secret_name = secret_req["name"]
        required_keys = set(secret_req["required_keys"])
        available_keys = workspace_inventory.get(secret_name)
        if available_keys is None:
            missing.append(f"missing secret: {secret_name}")
            continue
        for key in sorted(required_keys):
            if key not in available_keys:
                missing.append(f"missing key: {secret_name}.{key}")
    return len(missing) == 0, missing


async def build_action_contexts(
    *,
    role: Role,
    action_names: list[str] | None = None,
    query: str | None = None,
) -> list[ActionContextResponse]:
    """Build full authoring context (schema + secrets + example) for actions.

    Resolve actions by explicit name list, or by search ``query`` when no names
    are given. Unknown action names are skipped.
    """
    resolved_names = list(action_names) if action_names else []

    scopes = role.scopes or frozenset()
    # Gate the secret/OAuth inventory reads the same way build_secret_hints/
    # build_variable_hints do: do not enumerate workspace secret names/keys or
    # connected integrations to an author who lacks secret:read/integration:read.
    # Without the scope the inventory is empty, so configured/missing_requirements
    # never reveal which credentials this workspace actually has.
    workspace_inventory = (
        await load_secret_inventory(role) if has_scope(scopes, "secret:read") else {}
    )
    oauth_inventory = (
        await load_oauth_inventory(role)
        if has_scope(scopes, "integration:read")
        else set()
    )
    action_contexts: list[ActionContextResponse] = []
    async with RegistryActionsService.with_session(role=role) as registry_svc:
        if not resolved_names and query:
            entries = await registry_svc.search_actions_from_index(query, limit=20)
            resolved_names = [f"{entry.namespace}.{entry.name}" for entry, _ in entries]
        for action_name in resolved_names:
            indexed = await registry_svc.get_action_from_index(action_name)
            if indexed is None:
                continue
            tool = await create_tool_from_registry(action_name, indexed)
            requirements = secrets_to_requirements(
                registry_svc.aggregate_secrets_from_manifest(
                    indexed.manifest, action_name
                )
            )
            configured, missing = evaluate_configuration(
                requirements, workspace_inventory, oauth_inventory
            )
            action_contexts.append(
                ActionContextResponse(
                    action_name=action_name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters_json_schema,
                    required_secrets=requirements,
                    configured=configured,
                    missing_requirements=missing,
                    optional_secrets=optional_secret_names(requirements),
                    examples=[build_example_from_schema(tool.parameters_json_schema)],
                )
            )
    return action_contexts


async def build_variable_hints(*, role: Role) -> list[dict[str, Any]]:
    """List workspace variables in the default environment as hints.

    Returns ``[]`` when the caller lacks ``variable:read`` so the full set of
    variable names+keys is not enumerated to an unauthorized author.
    """
    if not has_scope(role.scopes or frozenset(), "variable:read"):
        return []
    async with VariablesService.with_session(role=role) as var_svc:
        variables = await var_svc.list_variables(
            environment=DEFAULT_SECRETS_ENVIRONMENT
        )
        return [
            {
                "name": var.name,
                "keys": sorted(var.values.keys()),
                "environment": var.environment,
            }
            for var in variables
        ]


async def build_secret_hints(*, role: Role) -> list[dict[str, Any]]:
    """List workspace secrets in the default environment as hints.

    Returns ``[]`` when the caller lacks ``secret:read`` so the full set of
    secret names+keys is not enumerated to an unauthorized author.
    """
    if not has_scope(role.scopes or frozenset(), "secret:read"):
        return []
    workspace_inventory = await load_secret_inventory(role)
    return [
        {
            "name": secret_name,
            "keys": sorted(keys),
            "environment": DEFAULT_SECRETS_ENVIRONMENT,
        }
        for secret_name, keys in workspace_inventory.items()
    ]


async def build_enabled_models(*, role: Role) -> list[EnabledModelInfo]:
    """List the models enabled for the workspace as authoring hints.

    These are the only models an author may select when configuring AI actions
    (``ai.agent``, ``ai.call``, ...) or agent presets, so the chat agent should
    pick a ``catalog_id`` from this list rather than guessing a model name.

    Returns ``[]`` when the caller lacks ``agent:read`` (mirroring the
    variable/secret hint gating) or when the role has no bound workspace, so the
    enabled-model set is never enumerated to an unauthorized author.
    """
    if not has_scope(role.scopes or frozenset(), "agent:read"):
        return []
    if role.workspace_id is None:
        return []
    async with AgentModelAccessService.with_session(role=role) as svc:
        models = await svc.get_workspace_models(role.workspace_id)
    return [
        EnabledModelInfo(
            catalog_id=str(model.id),
            model_name=model.model_name,
            model_provider=model.model_provider,
        )
        for model in models
    ]
