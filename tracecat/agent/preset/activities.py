from __future__ import annotations

import uuid

import sqlalchemy as sa
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog


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


@activity.defn
async def resolve_agent_preset_config_activity(
    args: ResolveAgentPresetConfigActivityInput,
) -> AgentConfigPayload:
    async with AgentPresetService.with_session(role=args.role) as service:
        config = await service.resolve_agent_preset_config(
            preset_id=args.preset_id,
            slug=args.preset_slug,
            preset_version_id=args.preset_version_id,
            preset_version=args.preset_version,
        )
        return agent_config_to_payload(config)


@activity.defn
async def resolve_agent_preset_version_ref_activity(
    args: ResolveAgentPresetVersionRefActivityInput,
) -> AgentPresetVersionRef:
    async with AgentPresetService.with_session(role=args.role) as service:
        version = await service.resolve_agent_preset_version(
            slug=args.preset_slug,
            preset_version=args.preset_version,
        )
        return AgentPresetVersionRef(
            preset_id=version.preset_id,
            preset_version_id=version.id,
        )


class CustomModelProviderConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str
    passthrough: bool = False


@activity.defn
async def resolve_custom_model_provider_config_activity(
    role: Role,
    catalog_id: uuid.UUID | None = None,
    use_workspace_credentials: bool = False,  # noqa: ARG001 - signature compatibility
) -> CustomModelProviderConfigResult:
    """Resolve custom-model-provider runtime config.

    Two paths:

    1. **v2 (preferred).** When ``catalog_id`` is a UUID, verify the catalog
       row is backed by a custom provider and resolve credentials through the
       catalog credential loader. That keeps org/workspace model-access checks
       and provider config decryption centralized.
    2. **Legacy.** When ``catalog_id`` is ``None`` (pre-v2 workflow history
       replay or DSL AI actions that don't carry a catalog_id yet), fall
       back to resolving from
       ``agent-custom-model-provider-credentials`` via
       ``get_runtime_provider_credentials``. This path still works on orgs
       whose legacy secret wasn't migrated; on migrated orgs it returns
       ``None`` and we error out with a clear message so the caller knows
       to configure a v2 catalog row.

    ``catalog_id`` remains nullable for legacy no-catalog executions.
    ``use_workspace_credentials`` is retained for activity signature
    compatibility with workflow code that scheduled the third argument before
    the catalog cutover; credential scope is now resolved by catalog id or the
    legacy runtime provider lookup.
    """
    activity.logger.info("Resolving custom model provider config")

    async with AgentManagementService.with_session(role) as svc:
        creds = await _load_custom_model_provider_creds(
            svc,
            catalog_id=catalog_id,
        )

    if creds is None:
        activity.logger.error("Custom model provider credentials not found")
        raise ApplicationError("Invalid custom model provider credentials")
    if not (base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL")):
        activity.logger.error(
            "Custom model provider base URL missing",
            extra={
                "has_model_name_override": bool(
                    creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
                ),
                "has_api_key": bool(creds.get("CUSTOM_MODEL_PROVIDER_API_KEY")),
            },
        )
        raise ApplicationError("Custom model provider base URL is required")
    passthrough = creds.get("CUSTOM_MODEL_PROVIDER_PASSTHROUGH", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    activity.logger.info(
        "Resolved custom model provider config",
        extra={
            "passthrough": passthrough,
            "has_model_name_override": bool(
                creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
            ),
            "has_api_key": bool(creds.get("CUSTOM_MODEL_PROVIDER_API_KEY")),
            "has_base_url": bool(base_url),
        },
    )
    return CustomModelProviderConfigResult(
        base_url=base_url,
        model_name=creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"),
        passthrough=passthrough,
    )


async def _load_custom_model_provider_creds(
    svc: AgentManagementService,
    *,
    catalog_id: uuid.UUID | None,
) -> dict[str, str] | None:
    """Return the dict shape ``_inject_provider_credentials`` expects.

    v2 path: verify ``catalog_id`` points to a custom-provider catalog row,
    then delegate to ``get_catalog_credentials`` so model-access checks and
    credential projection stay centralized.

    Legacy path: delegate to ``get_runtime_provider_credentials`` which
    reads the legacy ``agent-custom-model-provider-credentials`` secret.
    """
    if catalog_id is None:
        credentials = await svc.get_runtime_provider_credentials(
            "custom-model-provider"
        )
        if credentials is not None:
            return credentials
        return await svc.get_workspace_provider_credentials("custom-model-provider")

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
    if catalog_row is None or catalog_row.custom_provider_id is None:
        # The caller passed a catalog_id that isn't a custom-provider row.
        # Don't silently fall back to the legacy secret — that'd bind the
        # wrong provider's config to this workflow. Let the activity raise
        # its standard "credentials not found" error instead.
        return None

    return await svc.get_catalog_credentials(catalog_id)
