from __future__ import annotations

import uuid

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.preset.resolved_refs import ResolvedRefs, merge_resolved_refs
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    resolve_agents_config,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog, AgentTurnProvenance
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger


class ResolveAgentPresetConfigActivityInput(BaseModel):
    role: Role
    preset_slug: str | None = None
    preset_id: uuid.UUID | None = None
    preset_version_id: uuid.UUID | None = None
    preset_version: int | None = None
    session_id: uuid.UUID | None = None
    wf_exec_id: str | None = None

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
    model_config = ConfigDict(extra="ignore")

    role: Role
    preset_slug: str
    session_id: uuid.UUID | None = None
    wf_exec_id: str | None = None


class AgentPresetVersionRef(BaseModel):
    preset_id: uuid.UUID
    preset_version_id: uuid.UUID


class ResolveAgentsConfigActivityInput(BaseModel):
    role: Role
    agents: AgentSubagentsConfig = Field(default_factory=AgentSubagentsConfig)
    parent_preset_id: uuid.UUID | None = None
    parent_slug: str | None = None
    # Legacy Temporal payload field. ResourceHead resolution is unconditional.
    follow_latest_versions: bool | None = None
    # NOTE(ENG-1526): optional-with-default so pre-existing workflow histories
    # deserialize; only the durable resume path sets it. PR 2.3a (dispatch-time
    # resolution) may subsume or remove this flag.
    preserve_resolved_versions: bool = False
    session_id: uuid.UUID | None = None
    wf_exec_id: str | None = None
    parent_resolved_refs: ResolvedRefs | None = None


async def _write_agent_turn_provenance(
    service: AgentPresetService | AgentManagementService,
    *,
    session_id: uuid.UUID | None,
    wf_exec_id: str | None,
    resolved_refs: ResolvedRefs | None,
) -> None:
    """Persist an append-only per-turn resolution snapshot when identifiers exist.

    A turn may accumulate more than one snapshot: root-preset resolution
    always records the root refs, and the later subagent-resolution activity
    appends a merged snapshot when it runs. The row with the highest
    ``surrogate_id`` for a ``wf_exec_id`` is the final snapshot; earlier rows
    preserve provenance for turns that fail between the two activities.

    Not idempotent by design: the calling activities are scheduled with
    ``activity:fail_fast`` (``maximum_attempts=1``), so no Temporal retry can
    re-execute a committed insert, and replay reads activity results from
    history instead of re-running them. Revisit if a caller ever gains a
    retry policy.
    """

    if session_id is None or wf_exec_id is None or resolved_refs is None:
        return
    workspace_id = service.role.workspace_id
    if workspace_id is None:
        return

    service.session.add(
        AgentTurnProvenance(
            workspace_id=workspace_id,
            session_id=session_id,
            wf_exec_id=wf_exec_id,
            resolved_refs=resolved_refs.model_dump(mode="json"),
        )
    )
    await service.session.commit()


@activity.defn
async def resolve_agent_preset_config_activity(
    args: ResolveAgentPresetConfigActivityInput,
) -> AgentConfigPayload:
    async with AgentManagementService.with_session(role=args.role) as service:

        async def write_failure_refs(_err: TracecatNotFoundError) -> None:
            if service.presets is None:
                return
            failure_refs = await service.presets.build_root_preset_failure_refs(
                preset_id=args.preset_id,
                slug=args.preset_slug,
                preset_version_id=args.preset_version_id,
                preset_version=args.preset_version,
            )
            await _write_agent_turn_provenance(
                service,
                session_id=args.session_id,
                wf_exec_id=args.wf_exec_id,
                resolved_refs=failure_refs,
            )

        async def write_root_refs(resolved: AgentConfig) -> None:
            # Always record root refs at resolution-success time, before
            # credential loading and even when a later subagent activity
            # appends a merged snapshot: anything that fails afterwards
            # (provider credentials, custom-provider config, session load,
            # subagent resolution) would otherwise leave the turn with no
            # provenance row.
            await _write_agent_turn_provenance(
                service,
                session_id=args.session_id,
                wf_exec_id=args.wf_exec_id,
                resolved_refs=resolved.resolved_refs,
            )

        config: AgentConfig | None = None
        async with service.with_preset_config(
            preset_id=args.preset_id,
            slug=args.preset_slug,
            preset_version_id=args.preset_version_id,
            preset_version=args.preset_version,
            on_resolution_error=write_failure_refs,
            on_resolved=write_root_refs,
        ) as resolved_config:
            config = resolved_config

        assert config is not None
        return agent_config_to_payload(config)


@activity.defn
async def resolve_agent_preset_version_ref_activity(
    args: ResolveAgentPresetVersionRefActivityInput,
) -> AgentPresetVersionRef:
    async with AgentPresetService.with_session(role=args.role) as service:
        try:
            version = await service.resolve_agent_preset_version(
                slug=args.preset_slug,
            )
        except TracecatNotFoundError:
            # DSL preflight failures happen before the agent child workflow
            # exists; record the classified failure refs so the turn still
            # leaves provenance, then let the domain error propagate.
            try:
                failure_refs = await service.build_root_preset_failure_refs(
                    slug=args.preset_slug,
                )
                await _write_agent_turn_provenance(
                    service,
                    session_id=args.session_id,
                    wf_exec_id=args.wf_exec_id,
                    resolved_refs=failure_refs,
                )
            except Exception:
                # A provenance write failure must never mask the domain error.
                logger.exception("Failed to record preset preflight failure refs")
            raise
        return AgentPresetVersionRef(
            preset_id=version.preset_id,
            preset_version_id=version.id,
        )


@activity.defn
async def resolve_agents_config_activity(
    args: ResolveAgentsConfigActivityInput,
) -> ResolvedAgentsRuntimeConfig:
    async with AgentPresetService.with_session(role=args.role) as service:
        resolved = await resolve_agents_config(
            service,
            agents=args.agents,
            parent_preset_id=args.parent_preset_id,
            parent_slug=args.parent_slug,
            include_runtime_config=True,
            preserve_resolved_versions=args.preserve_resolved_versions,
        )
        runtime_config = resolved.to_runtime_config()
        runtime_config.resolved_refs = merge_resolved_refs(
            args.parent_resolved_refs,
            runtime_config.resolved_refs,
        )
        await _write_agent_turn_provenance(
            service,
            session_id=args.session_id,
            wf_exec_id=args.wf_exec_id,
            resolved_refs=runtime_config.resolved_refs,
        )
        return runtime_config


class CustomModelProviderConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str
    passthrough: bool = False


@activity.defn
async def resolve_custom_model_provider_config_activity(
    role: Role | dict[str, object],
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
       replay or DSL AI actions that don't carry a catalog_id yet), resolve
       workspace-scoped ``agent-custom-model-provider-credentials``.

    ``catalog_id`` remains nullable for legacy no-catalog executions.
    ``use_workspace_credentials`` is retained for activity signature
    compatibility with workflow code that scheduled the third argument before
    the catalog cutover; credential scope is now resolved by catalog id or the
    legacy runtime provider lookup.
    """
    activity.logger.info("Resolving custom model provider config")

    role = role if isinstance(role, Role) else Role.model_validate(role)
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

    Legacy path: no catalog id means workspace-scoped provider credentials.
    """
    if catalog_id is None:
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
