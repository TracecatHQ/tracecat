from __future__ import annotations

import uuid

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from temporalio import activity

from tracecat.agent.error_policy import (
    agent_preparation_failed,
    invalid_agent_configuration,
)
from tracecat.agent.gateway_providers import (
    CUSTOM_MODEL_PROVIDER_SLUG,
    is_builtin_gateway_provider,
    is_gateway_provider,
    resolve_gateway_provider_config,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    resolve_agents_config,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.temporal.errors import raise_application_error_from_classification


class ResolveAgentPresetConfigActivityInput(BaseModel):
    role: Role
    preset_slug: str | None = None
    preset_id: uuid.UUID | None = None
    preset_version_id: uuid.UUID | None = None
    preset_version: int | None = None

    @model_validator(mode="after")
    def ensure_identifier(self) -> ResolveAgentPresetConfigActivityInput:
        if (
            self.preset_slug is None
            and self.preset_id is None
            and self.preset_version_id is None
        ):
            raise ValueError(
                "Either preset_slug, preset_id, or preset_version_id must be provided"
            )
        return self


class ResolveAgentPresetVersionRefActivityInput(BaseModel):
    role: Role
    preset_slug: str
    preset_version: int | None = None


class AgentPresetVersionRef(BaseModel):
    preset_id: uuid.UUID
    preset_version_id: uuid.UUID


class ResolveAgentsConfigActivityInput(BaseModel):
    role: Role
    agents: AgentSubagentsConfig = Field(default_factory=AgentSubagentsConfig)
    parent_preset_id: uuid.UUID | None = None
    parent_slug: str | None = None
    follow_latest_versions: bool | None = None


@activity.defn
async def resolve_agent_preset_config_activity(
    args: ResolveAgentPresetConfigActivityInput,
) -> AgentConfigPayload:
    try:
        async with AgentManagementService.with_session(role=args.role) as service:
            async with service.with_preset_config(
                preset_id=args.preset_id,
                slug=args.preset_slug,
                preset_version_id=args.preset_version_id,
                preset_version=args.preset_version,
            ) as config:
                return agent_config_to_payload(config)
    except (
        TracecatAuthorizationError,
        TracecatNotFoundError,
        TracecatValidationError,
    ) as exc:
        raise_application_error_from_classification(invalid_agent_configuration(exc))


@activity.defn
async def resolve_agent_preset_version_ref_activity(
    args: ResolveAgentPresetVersionRefActivityInput,
) -> AgentPresetVersionRef:
    async with AgentPresetService.with_session(role=args.role) as service:
        version = await service.resolve_agent_preset_version(
            slug=args.preset_slug,
        )
        return AgentPresetVersionRef(
            preset_id=version.preset_id,
            preset_version_id=version.id,
        )


@activity.defn
async def resolve_agents_config_activity(
    args: ResolveAgentsConfigActivityInput,
) -> ResolvedAgentsRuntimeConfig:
    try:
        async with AgentPresetService.with_session(role=args.role) as service:
            # ``False`` is reserved for rebuilding an already-resolved session
            # binding. Fresh executions (including legacy payloads with ``None``)
            # always follow child heads.
            follow_latest_versions = args.follow_latest_versions is not False
            resolved = await resolve_agents_config(
                service,
                agents=args.agents,
                parent_preset_id=args.parent_preset_id,
                parent_slug=args.parent_slug,
                include_runtime_config=True,
                follow_latest_versions=follow_latest_versions,
            )
            return resolved.to_runtime_config()
    except ValidationError as exc:
        raise_application_error_from_classification(
            agent_preparation_failed(exc, retryable=False)
        )
    except (
        TracecatAuthorizationError,
        TracecatNotFoundError,
        TracecatValidationError,
    ) as exc:
        raise_application_error_from_classification(invalid_agent_configuration(exc))


class CustomModelProviderConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str
    passthrough: bool = False


@activity.defn
async def resolve_custom_model_provider_config_activity(
    role: Role | dict[str, object],
    catalog_id: uuid.UUID | None = None,
    use_workspace_credentials: bool = False,  # noqa: ARG001 - signature compatibility
    model_provider: str = CUSTOM_MODEL_PROVIDER_SLUG,
) -> CustomModelProviderConfigResult:
    """Resolve runtime config for an OpenAI-compatible gateway provider.

    Applies to ``custom-model-provider`` and the built-in gateway providers
    (Ollama, vLLM, LiteLLM, OpenRouter). Two paths:

    1. **v2 (preferred).** When ``catalog_id`` is a UUID, verify the catalog
       row belongs to ``model_provider`` and resolve credentials through the
       catalog credential loader. That keeps org/workspace model-access checks
       and provider config decryption centralized.
    2. **Legacy.** When ``catalog_id`` is ``None`` (pre-v2 workflow history
       replay or DSL AI actions that don't carry a catalog_id yet), resolve
       workspace-scoped ``agent-{model_provider}-credentials``.

    ``catalog_id`` remains nullable for legacy no-catalog executions.
    ``use_workspace_credentials`` is retained for activity signature
    compatibility with workflow code that scheduled the third argument before
    the catalog cutover; credential scope is now resolved by catalog id or the
    legacy runtime provider lookup.
    """
    activity.logger.info(
        "Resolving gateway provider config", extra={"provider": model_provider}
    )
    if not is_gateway_provider(model_provider):
        activity.logger.error(
            "Provider does not support passthrough configuration",
            extra={"provider": model_provider},
        )
        raise_application_error_from_classification(invalid_agent_configuration())

    role = role if isinstance(role, Role) else Role.model_validate(role)
    async with AgentManagementService.with_session(role) as svc:
        try:
            creds = await _load_custom_model_provider_creds(
                svc,
                catalog_id=catalog_id,
                model_provider=model_provider,
            )
        except TracecatAuthorizationError as exc:
            raise_application_error_from_classification(
                invalid_agent_configuration(exc)
            )

    if creds is None:
        activity.logger.error(
            "Gateway provider credentials not found",
            extra={"provider": model_provider},
        )
        raise_application_error_from_classification(invalid_agent_configuration())
    runtime = resolve_gateway_provider_config(model_provider, creds)
    if runtime is None or not runtime.base_url:
        activity.logger.error(
            "Gateway provider base URL missing",
            extra={
                "provider": model_provider,
                "has_model_name_override": bool(runtime and runtime.model_name),
                "has_api_key": bool(runtime and runtime.api_key),
            },
        )
        raise_application_error_from_classification(invalid_agent_configuration())
    activity.logger.info(
        "Resolved gateway provider config",
        extra={
            "provider": model_provider,
            "passthrough": runtime.passthrough,
            "has_model_name_override": bool(runtime.model_name),
            "has_api_key": bool(runtime.api_key),
            "has_base_url": True,
        },
    )
    return CustomModelProviderConfigResult(
        base_url=runtime.base_url,
        model_name=runtime.model_name,
        passthrough=runtime.passthrough,
    )


async def _load_custom_model_provider_creds(
    svc: AgentManagementService,
    *,
    catalog_id: uuid.UUID | None,
    model_provider: str = CUSTOM_MODEL_PROVIDER_SLUG,
) -> dict[str, str] | None:
    """Return the dict shape ``_inject_provider_credentials`` expects.

    v2 path: verify ``catalog_id`` points at a row for ``model_provider`` (a
    custom-provider row, or a built-in gateway provider row), then delegate to
    ``get_catalog_credentials`` so model-access checks and credential
    projection stay centralized.

    Legacy path: no catalog id means workspace-scoped provider credentials.
    """
    if catalog_id is None:
        return await svc.get_workspace_provider_credentials(model_provider)

    catalog_row = (
        await svc.session.execute(
            select(AgentCatalog).where(
                AgentCatalog.id == catalog_id,
                sa.or_(
                    AgentCatalog.organization_id.is_(None),
                    AgentCatalog.organization_id == svc.organization_id,
                ),
            )
        )
    ).scalar_one_or_none()
    if catalog_row is None:
        return None
    is_custom_row = (
        model_provider == CUSTOM_MODEL_PROVIDER_SLUG
        and catalog_row.custom_provider_id is not None
    )
    is_builtin_row = (
        is_builtin_gateway_provider(model_provider)
        and catalog_row.model_provider == model_provider
    )
    if not (is_custom_row or is_builtin_row):
        # The caller passed a catalog_id for a different provider. Don't
        # silently fall back to the legacy secret — that'd bind the wrong
        # provider's config to this workflow. Let the activity raise its
        # standard "credentials not found" error instead.
        return None

    return await svc.get_catalog_credentials(catalog_id)
