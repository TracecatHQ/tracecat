"""Services for AI SPM APIs."""

from __future__ import annotations

import hashlib
import importlib.util
import secrets
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    SpmEndpoint,
    SpmEnforcementTask,
    SpmFinding,
    SpmFindingDecision,
    SpmInventoryItem,
    SpmInventoryObservation,
    SpmInventoryRelationship,
    SpmResponseActionPreview,
)
from tracecat.exceptions import EntitlementRequired
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseOrgService
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.spm.exceptions import (
    SpmAuthenticationError,
    SpmConflictError,
    SpmControlCatalogError,
    SpmNotFoundError,
)
from tracecat_ee.spm.intel import (
    BestEffortSpmThreatIntelProvider,
    SpmThreatIntelProvider,
)
from tracecat_ee.spm.schemas import (
    SpmAnyControlInventoryItemData,
    SpmConfigControlData,
    SpmControlContext,
    SpmControlPolicy,
    SpmControlRead,
    SpmControlResult,
    SpmDirectoryControlData,
    SpmEndpointCreate,
    SpmEndpointCreateResponse,
    SpmEndpointInventoryItemRead,
    SpmEndpointRead,
    SpmEndpointSyncRequest,
    SpmEndpointSyncResponse,
    SpmEnforcementTaskRead,
    SpmFindingDecisionCreate,
    SpmFindingDecisionRead,
    SpmFindingQueryParams,
    SpmFindingRead,
    SpmHookControlData,
    SpmInstructionFileControlData,
    SpmInventoryItemRead,
    SpmInventoryQueryParams,
    SpmInventoryTaxonomyRead,
    SpmMcpServerControlData,
    SpmResponseActionPreviewCreate,
    SpmResponseActionPreviewRead,
    SpmResponseActionRead,
    SpmSkillControlData,
    SpmSyncInventoryItemUpsert,
    SpmSyncInventoryRelationshipUpsert,
    SpmSyncResponseActionPreviewResult,
    SpmSyncTaskResult,
)
from tracecat_ee.spm.taxonomy import (
    inventory_taxonomy_as_dict,
    validate_control_target,
)
from tracecat_ee.spm.types import (
    SpmEndpointComplianceStatus,
    SpmEnforcementAction,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
    SpmInventoryItemType,
    SpmResponseActionPreviewStatus,
    SpmSyncTaskResultStatus,
)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _apply_desc_cursor_filter(
    stmt: sa.Select[Any],
    *,
    model: type[Any],
    cursor: str | None,
    sort_attr: str,
) -> sa.Select[Any]:
    if not cursor:
        return stmt
    cursor_data = BaseCursorPaginator.decode_cursor(cursor)
    sort_value = cursor_data.sort_value
    if sort_value is None:
        raise ValueError("Cursor missing sort value")
    cursor_id = uuid.UUID(cursor_data.id)
    sort_column = getattr(model, sort_attr)
    return stmt.where(
        sa.or_(
            sort_column < sort_value,
            sa.and_(sort_column == sort_value, model.id < cursor_id),
        )
    )


def _apply_desc_cursor_filter_columns(
    stmt: sa.Select[Any],
    *,
    cursor: str | None,
    sort_column: Any,
    id_column: Any,
) -> sa.Select[Any]:
    if not cursor:
        return stmt
    cursor_data = BaseCursorPaginator.decode_cursor(cursor)
    sort_value = cursor_data.sort_value
    if sort_value is None:
        raise ValueError("Cursor missing sort value")
    cursor_id = uuid.UUID(cursor_data.id)
    return stmt.where(
        sa.or_(
            sort_column < sort_value,
            sa.and_(sort_column == sort_value, id_column < cursor_id),
        )
    )


type SpmControlCheckFn = Callable[[SpmControlContext], SpmControlResult]


@dataclass(frozen=True, slots=True)
class SpmControlDefinition:
    """Loaded SPM control metadata and executable check."""

    control: SpmControlRead
    check: SpmControlCheckFn
    metadata_path: Path
    check_path: Path


_RESPONSE_ACTION_CATALOG: tuple[SpmResponseActionRead, ...] = (
    SpmResponseActionRead(
        key=SpmEnforcementAction.DISABLE_MCP_SERVER,
        title="Disable MCP server",
        description=(
            "Disable a configured Claude MCP server on a writable local settings "
            "surface. Project .mcp.json entries are shadowed through "
            "disabledMcpjsonServers instead of rewriting .mcp.json."
        ),
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.MCP_SERVER],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="claude_json_or_project_local_settings",
        payload_fields=[
            "server_name",
            "resolved_identity",
            "source_path",
            "project_root",
        ],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.EXCLUDE_INSTRUCTION_FILE,
        title="Exclude instruction file",
        description=(
            "Exclude a Claude instruction file by adding its path to "
            "claudeMdExcludes on a writable user or project-local settings file."
        ),
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.INSTRUCTION_FILE],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="settings_json_or_project_local_settings",
        payload_fields=["file_path", "project_root", "target_path"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.REVOKE_TRUSTED_DIRECTORY,
        title="Revoke trusted directory",
        description="Remove a trusted directory entry from Claude user state.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.TRUSTED_DIRECTORY],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="claude_json",
        payload_fields=["directory_path", "target_path"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.REVOKE_ADDITIONAL_DIRECTORY,
        title="Revoke additional directory",
        description="Remove an additional directory entry from Claude user state.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.ADDITIONAL_DIRECTORY],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="claude_json",
        payload_fields=["directory_path", "target_path"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.RECONCILE_PERMISSION_CONFIG,
        title="Reconcile permission config",
        description="Replace the Claude permissions value with the approved policy value.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.PERMISSION_CONFIG],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="writable_claude_settings",
        payload_fields=["target_path", "value"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.RECONCILE_SANDBOX_CONFIG,
        title="Reconcile sandbox config",
        description="Replace the Claude sandbox value with the approved policy value.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.SANDBOX_CONFIG],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="writable_claude_settings",
        payload_fields=["target_path", "value"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.DISABLE_HOOK,
        title="Disable hook",
        description="Remove a matching Claude hook entry from writable settings.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.HOOK],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="writable_claude_settings",
        payload_fields=["fingerprint", "target_path"],
        disruptive=True,
    ),
    SpmResponseActionRead(
        key=SpmEnforcementAction.DISABLE_SKILL,
        title="Disable skill",
        description="Remove a matching Claude skill entry from writable settings.",
        harness=SpmHarness.CLAUDE_CODE,
        item_types=[SpmInventoryItemType.SKILL],
        execution_mode="endpoint_sync",
        preview_supported=True,
        target_surface="writable_claude_settings",
        payload_fields=["fingerprint", "name", "target_path"],
        disruptive=True,
    ),
)


def get_response_action(
    action: str | SpmEnforcementAction,
) -> SpmResponseActionRead | None:
    """Fetch a static response action catalog entry by key."""
    ref = action.value if isinstance(action, SpmEnforcementAction) else action
    for entry in _RESPONSE_ACTION_CATALOG:
        if entry.key.value == ref:
            return entry
    return None


def load_control_catalog_from_directory(
    directory: str | Path,
) -> tuple[SpmControlRead, ...]:
    """Load and validate an SPM control catalog from a directory tree."""
    return tuple(
        definition.control
        for definition in _load_control_definitions_from_directory(Path(directory))
    )


@lru_cache(maxsize=1)
def _get_control_definitions() -> tuple[SpmControlDefinition, ...]:
    return _load_control_definitions_from_directory(_builtin_control_directory())


def get_control_catalog() -> tuple[SpmControlRead, ...]:
    """Return built-in static SPM control metadata."""
    return tuple(definition.control for definition in _get_control_definitions())


def get_control(control_ref: str | uuid.UUID) -> SpmControlRead | None:
    """Fetch a control by UUID, current key, or alias."""
    if definition := _get_control_definition(control_ref):
        return definition.control
    return None


def _builtin_control_directory() -> Path:
    return Path(__file__).parent / "controls"


def _load_control_definitions_from_directory(
    directory: Path,
) -> tuple[SpmControlDefinition, ...]:
    manifest_paths = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    if not manifest_paths:
        raise SpmControlCatalogError(
            "SPM control catalog is empty.",
            code="spm_control_catalog_empty",
            path=directory,
        )

    definitions: list[SpmControlDefinition] = []
    seen_ids: dict[uuid.UUID, Path] = {}
    seen_refs: dict[str, Path] = {}

    for manifest_path in manifest_paths:
        raw_manifest = _read_control_manifest(manifest_path)
        try:
            control = SpmControlRead.model_validate(raw_manifest)
        except ValidationError as exc:
            raise SpmControlCatalogError(
                "Invalid SPM control manifest.",
                code="spm_control_manifest_invalid",
                path=manifest_path,
            ) from exc
        try:
            validate_control_target(
                harness=control.harness,
                item_type=control.item_type,
                source_types=control.source_types,
            )
        except ValueError as exc:
            raise SpmControlCatalogError(
                "SPM control target is not in the inventory taxonomy.",
                code="spm_control_target_invalid",
                path=manifest_path,
                ref=control.key,
            ) from exc

        if control.id in seen_ids:
            raise SpmControlCatalogError(
                "Duplicate SPM control id.",
                code="spm_control_id_duplicate",
                path=manifest_path,
                ref=control.id,
            )
        seen_ids[control.id] = manifest_path

        for ref in (control.key, *control.aliases):
            if not ref:
                raise SpmControlCatalogError(
                    "Empty SPM control key or alias.",
                    code="spm_control_ref_empty",
                    path=manifest_path,
                )
            if ref in seen_refs:
                raise SpmControlCatalogError(
                    "Duplicate SPM control key or alias.",
                    code="spm_control_ref_duplicate",
                    path=manifest_path,
                    ref=ref,
                    existing_path=seen_refs[ref],
                )
            seen_refs[ref] = manifest_path

        check_path = manifest_path.with_suffix(".py")
        if not check_path.exists():
            raise SpmControlCatalogError(
                "Missing SPM control check file.",
                code="spm_control_check_missing",
                path=check_path,
                ref=control.key,
            )

        definitions.append(
            SpmControlDefinition(
                control=control,
                check=_load_control_check(control.id, check_path),
                metadata_path=manifest_path,
                check_path=check_path,
            )
        )

    return tuple(sorted(definitions, key=lambda definition: definition.control.key))


def _read_control_manifest(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        raw_manifest = yaml.safe_load(handle)
    if not isinstance(raw_manifest, dict):
        raise SpmControlCatalogError(
            "SPM control manifest must be an object.",
            code="spm_control_manifest_not_object",
            path=manifest_path,
        )
    return raw_manifest


def _load_control_check(control_id: uuid.UUID, check_path: Path) -> SpmControlCheckFn:
    module_name = f"_tracecat_spm_control_{control_id.hex}"
    spec = importlib.util.spec_from_file_location(module_name, check_path)
    if spec is None or spec.loader is None:
        raise SpmControlCatalogError(
            "Unable to load SPM control check file.",
            code="spm_control_check_load_failed",
            path=check_path,
            ref=control_id,
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    check = getattr(module, "check", None)
    if not callable(check):
        raise SpmControlCatalogError(
            "SPM control check must define check(ctx).",
            code="spm_control_check_invalid",
            path=check_path,
            ref=control_id,
        )
    return cast(SpmControlCheckFn, check)


def _get_control_definition(
    control_ref: str | uuid.UUID,
) -> SpmControlDefinition | None:
    ref = str(control_ref)
    for definition in _get_control_definitions():
        control = definition.control
        if ref == str(control.id) or ref == control.key or ref in control.aliases:
            return definition
    return None


@lru_cache(maxsize=1)
def _control_definitions_by_target() -> dict[
    tuple[str, str], tuple[SpmControlDefinition, ...]
]:
    grouped: dict[tuple[str, str], list[SpmControlDefinition]] = {}
    for definition in _get_control_definitions():
        control = definition.control
        target = (
            control.harness.value,
            control.item_type.value,
        )
        grouped.setdefault(target, []).append(definition)
    return {
        target: tuple(sorted(items, key=lambda definition: definition.control.key))
        for target, items in grouped.items()
    }


def _controls_for_item(item: SpmInventoryItem) -> tuple[SpmControlDefinition, ...]:
    definitions = _control_definitions_by_target().get(
        (item.harness, item.item_type), ()
    )
    return tuple(
        definition
        for definition in definitions
        if not definition.control.source_types
        or item.source_type
        in {source_type.value for source_type in definition.control.source_types}
    )


def _control_id_for_ref(control_ref: str) -> uuid.UUID | None:
    if definition := _get_control_definition(control_ref):
        return definition.control.id
    try:
        return uuid.UUID(control_ref)
    except ValueError:
        return None


def _control_data_from_rows(
    *,
    item: SpmInventoryItem,
    observation: SpmInventoryObservation,
) -> SpmAnyControlInventoryItemData:
    metadata = item.item_metadata or {}
    evidence = observation.evidence or {}
    observed_state = observation.observed_state or {}
    base = {
        "id": item.id,
        "observation_id": observation.id,
        "identity_key": item.identity_key,
        "display_name": item.display_name,
        "harness": item.harness,
        "item_type": item.item_type,
        "source_type": item.source_type,
        "item_location": item.item_location,
        "source_location": item.source_location,
        "content_hash": observation.content_hash or item.content_hash,
        "metadata": metadata,
        "evidence": evidence,
        "observed_state": observed_state,
    }

    match item.item_type:
        case (
            SpmInventoryItemType.TRUSTED_DIRECTORY.value
            | SpmInventoryItemType.ADDITIONAL_DIRECTORY.value
        ):
            directory_path = (
                _string(metadata.get("directory_path")) or item.identity_key
            )
            return SpmDirectoryControlData.model_validate(
                {
                    **base,
                    "directory_path": directory_path,
                    "file_path": _string(metadata.get("file_path")),
                    "parse_status": _string(metadata.get("parse_status")),
                }
            )
        case (
            SpmInventoryItemType.PERMISSION_CONFIG.value
            | SpmInventoryItemType.SANDBOX_CONFIG.value
        ):
            return SpmConfigControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "value": observed_state.get("value"),
                }
            )
        case SpmInventoryItemType.MCP_SERVER.value:
            return SpmMcpServerControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "server_name": _string(metadata.get("server_name")),
                    "resolved_identity": _string(metadata.get("resolved_identity")),
                    "mcp_identity_key": _string(metadata.get("mcp_identity_key")),
                }
            )
        case SpmInventoryItemType.HOOK.value:
            return SpmHookControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "fingerprint": _string(metadata.get("fingerprint")),
                    "event": _string(metadata.get("event")),
                    "command": _string(metadata.get("command")),
                }
            )
        case SpmInventoryItemType.SKILL.value:
            return SpmSkillControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "fingerprint": _string(metadata.get("fingerprint")),
                    "name": _string(metadata.get("name")),
                    "skill": evidence.get("skill"),
                }
            )
        case SpmInventoryItemType.INSTRUCTION_FILE.value:
            return SpmInstructionFileControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "enforceable": _bool(metadata.get("enforceable")),
                    "language_signal": _dict(evidence.get("language_signal")),
                    "obfuscation": _dict(evidence.get("obfuscation")),
                    "urls": _string_list(evidence.get("urls")),
                    "domains": _string_list(evidence.get("domains")),
                    "ips": _string_list(evidence.get("ips")),
                }
            )

    raise ValueError(
        f"Unsupported SPM inventory item type for controls: {item.item_type}"
    )


def _string(raw: Any) -> str | None:
    return raw if isinstance(raw, str) else None


def _bool(raw: Any) -> bool | None:
    return raw if isinstance(raw, bool) else None


def _dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _endpoint_compliance_status(
    endpoint: SpmEndpoint,
    *,
    observation_count: int,
    open_finding_count: int,
    pending_finding_count: int,
) -> SpmEndpointComplianceStatus:
    if endpoint.last_sync_at is None or observation_count == 0:
        return SpmEndpointComplianceStatus.NOT_ASSESSED
    if open_finding_count > 0:
        return SpmEndpointComplianceStatus.NEEDS_ATTENTION
    if pending_finding_count > 0:
        return SpmEndpointComplianceStatus.ENFORCEMENT_QUEUED
    return SpmEndpointComplianceStatus.COMPLIANT


async def _endpoint_compliance_by_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    endpoints: list[SpmEndpoint],
) -> dict[uuid.UUID, SpmEndpointComplianceStatus]:
    endpoint_ids = [endpoint.id for endpoint in endpoints]
    if not endpoint_ids:
        return {}

    observation_counts = dict.fromkeys(endpoint_ids, 0)
    observation_stmt = (
        select(
            SpmInventoryObservation.endpoint_id,
            sa.func.count(SpmInventoryObservation.id),
        )
        .where(
            SpmInventoryObservation.organization_id == organization_id,
            SpmInventoryObservation.endpoint_id.in_(endpoint_ids),
        )
        .group_by(SpmInventoryObservation.endpoint_id)
    )
    for endpoint_id, count in (await session.execute(observation_stmt)).all():
        observation_counts[endpoint_id] = count

    finding_counts = {
        endpoint_id: {
            SpmFindingStatus.OPEN.value: 0,
            SpmFindingStatus.ENFORCEMENT_PENDING.value: 0,
        }
        for endpoint_id in endpoint_ids
    }
    finding_stmt = (
        select(
            SpmFinding.endpoint_id,
            SpmFinding.status,
            sa.func.count(SpmFinding.id),
        )
        .where(
            SpmFinding.organization_id == organization_id,
            SpmFinding.endpoint_id.in_(endpoint_ids),
            SpmFinding.status.in_(
                [
                    SpmFindingStatus.OPEN.value,
                    SpmFindingStatus.ENFORCEMENT_PENDING.value,
                ]
            ),
        )
        .group_by(SpmFinding.endpoint_id, SpmFinding.status)
    )
    for endpoint_id, status, count in (await session.execute(finding_stmt)).all():
        finding_counts[endpoint_id][status] = count

    return {
        endpoint.id: _endpoint_compliance_status(
            endpoint,
            observation_count=observation_counts[endpoint.id],
            open_finding_count=finding_counts[endpoint.id][SpmFindingStatus.OPEN.value],
            pending_finding_count=finding_counts[endpoint.id][
                SpmFindingStatus.ENFORCEMENT_PENDING.value
            ],
        )
        for endpoint in endpoints
    }


def _endpoint_read(
    endpoint: SpmEndpoint,
    compliance_status: SpmEndpointComplianceStatus,
) -> SpmEndpointRead:
    data = {
        field_name: getattr(endpoint, field_name)
        for field_name in SpmEndpointRead.model_fields
        if field_name != "compliance_status"
    }
    data["compliance_status"] = compliance_status
    return SpmEndpointRead.model_validate(data)


async def _endpoint_reads(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    endpoints: list[SpmEndpoint],
) -> list[SpmEndpointRead]:
    compliance_by_id = await _endpoint_compliance_by_id(
        session,
        organization_id=organization_id,
        endpoints=endpoints,
    )
    return [
        _endpoint_read(
            endpoint,
            compliance_by_id.get(
                endpoint.id,
                SpmEndpointComplianceStatus.NOT_ASSESSED,
            ),
        )
        for endpoint in endpoints
    ]


class SpmService(BaseOrgService):
    """Org-scoped service for SPM operator APIs."""

    service_name = "spm"

    async def list_response_actions(self) -> list[SpmResponseActionRead]:
        return list(_RESPONSE_ACTION_CATALOG)

    async def get_response_action(self, action: str) -> SpmResponseActionRead:
        if entry := get_response_action(action):
            return entry
        raise SpmNotFoundError(
            "SPM response action not found.",
            code="spm_response_action_not_found",
            action=action,
        )

    async def list_controls(self) -> list[SpmControlRead]:
        return list(get_control_catalog())

    async def get_control(self, control_id: str) -> SpmControlRead:
        if control := get_control(control_id):
            return control
        raise SpmNotFoundError(
            "SPM control not found.",
            code="spm_control_not_found",
            control_id=control_id,
        )

    async def get_inventory_taxonomy(self) -> SpmInventoryTaxonomyRead:
        return SpmInventoryTaxonomyRead.model_validate(inventory_taxonomy_as_dict())

    async def list_endpoints(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmEndpointRead]:
        stmt = select(SpmEndpoint).where(
            SpmEndpoint.organization_id == self.organization_id
        )
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmEndpoint,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmEndpoint.updated_at.desc(), SpmEndpoint.id.desc())
        stmt = stmt.limit(params.limit + 1)

        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmEndpointRead](
            items=await _endpoint_reads(
                self.session,
                organization_id=self.organization_id,
                endpoints=rows,
            ),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def create_endpoint(
        self,
        params: SpmEndpointCreate,
    ) -> SpmEndpointCreateResponse:
        enrollment_token = _issue_secret("tcspm_enroll")
        row = SpmEndpoint(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            name=params.name,
            harness=params.harness.value,
            platform=params.platform.value,
            status="pending",
            hostname=params.hostname,
            os_user=params.os_user,
            home_path=params.home_path,
            endpoint_version=params.endpoint_version,
            client_metadata=params.client_metadata,
            enrollment_token_hash=_hash_token(enrollment_token),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return SpmEndpointCreateResponse(
            endpoint=_endpoint_read(row, SpmEndpointComplianceStatus.NOT_ASSESSED),
            enrollment_token=enrollment_token,
        )

    async def get_endpoint(self, endpoint_id: uuid.UUID) -> SpmEndpointRead:
        row = await self._get_endpoint_row(endpoint_id)
        return (
            await _endpoint_reads(
                self.session,
                organization_id=self.organization_id,
                endpoints=[row],
            )
        )[0]

    async def delete_pending_endpoint(self, endpoint_id: uuid.UUID) -> None:
        row = await self._get_endpoint_row(endpoint_id)
        if not self._can_delete_pending_endpoint(row):
            raise SpmConflictError(
                "Only pending enrollments that have never enrolled or synced can be removed.",
                code="spm_endpoint_delete_conflict",
                endpoint_id=endpoint_id,
            )
        await self.session.delete(row)
        await self.session.commit()

    async def list_inventory(
        self,
        params: SpmInventoryQueryParams,
    ) -> CursorPaginatedResponse[SpmInventoryItemRead]:
        stmt = select(SpmInventoryItem).where(
            SpmInventoryItem.organization_id == self.organization_id
        )
        if params.harness is not None:
            stmt = stmt.where(SpmInventoryItem.harness == params.harness.value)
        if params.item_type is not None:
            stmt = stmt.where(SpmInventoryItem.item_type == params.item_type.value)
        if params.source_type is not None:
            stmt = stmt.where(SpmInventoryItem.source_type == params.source_type.value)
        if params.endpoint_id is not None:
            inventory_observation_exists = (
                select(SpmInventoryObservation.id)
                .where(
                    SpmInventoryObservation.organization_id == self.organization_id,
                    SpmInventoryObservation.endpoint_id == params.endpoint_id,
                    SpmInventoryObservation.inventory_item_id == SpmInventoryItem.id,
                )
                .exists()
            )
            stmt = stmt.where(inventory_observation_exists)
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmInventoryItem,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(
            SpmInventoryItem.updated_at.desc(),
            SpmInventoryItem.id.desc(),
        )
        stmt = stmt.limit(params.limit + 1)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmInventoryItemRead](
            items=SpmInventoryItemRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_endpoint_inventory(
        self,
        endpoint_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmEndpointInventoryItemRead]:
        await self._get_endpoint_row(endpoint_id)
        stmt = (
            select(
                SpmInventoryItem.id.label("inventory_item_id"),
                SpmInventoryObservation.id.label("inventory_observation_id"),
                SpmInventoryItem.organization_id.label("organization_id"),
                SpmInventoryObservation.endpoint_id.label("endpoint_id"),
                SpmInventoryObservation.workspace_id.label("workspace_id"),
                SpmInventoryItem.harness.label("harness"),
                SpmInventoryItem.item_type.label("item_type"),
                SpmInventoryItem.source_type.label("source_type"),
                SpmInventoryItem.item_location.label("item_location"),
                SpmInventoryItem.source_location.label("source_location"),
                SpmInventoryItem.identity_key.label("identity_key"),
                SpmInventoryItem.display_name.label("display_name"),
                SpmInventoryObservation.content_hash.label("content_hash"),
                SpmInventoryItem.item_metadata.label("item_metadata"),
                SpmInventoryObservation.evidence.label("evidence"),
                SpmInventoryObservation.observed_state.label("observed_state"),
                SpmInventoryObservation.first_seen_at.label("first_seen_at"),
                SpmInventoryObservation.last_seen_at.label("last_seen_at"),
            )
            .select_from(SpmInventoryObservation)
            .join(
                SpmInventoryItem,
                SpmInventoryItem.id == SpmInventoryObservation.inventory_item_id,
            )
            .where(
                SpmInventoryObservation.organization_id == self.organization_id,
                SpmInventoryObservation.endpoint_id == endpoint_id,
            )
        )
        stmt = _apply_desc_cursor_filter_columns(
            stmt,
            cursor=params.cursor,
            sort_column=SpmInventoryObservation.last_seen_at,
            id_column=SpmInventoryObservation.id,
        )
        stmt = stmt.order_by(
            SpmInventoryObservation.last_seen_at.desc(),
            SpmInventoryObservation.id.desc(),
        )
        stmt = stmt.limit(params.limit + 1)
        rows = list((await self.session.execute(stmt)).mappings().all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last["inventory_observation_id"],
                sort_column="last_seen_at",
                sort_value=last["last_seen_at"],
            )

        return CursorPaginatedResponse[SpmEndpointInventoryItemRead](
            items=SpmEndpointInventoryItemRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_inventory_item(
        self,
        inventory_item_id: uuid.UUID,
    ) -> SpmInventoryItemRead:
        stmt = select(SpmInventoryItem).where(
            SpmInventoryItem.id == inventory_item_id,
            SpmInventoryItem.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise SpmNotFoundError(
                "SPM inventory item not found.",
                code="spm_inventory_item_not_found",
                inventory_item_id=inventory_item_id,
            )
        return SpmInventoryItemRead.model_validate(row)

    async def list_findings(
        self,
        params: SpmFindingQueryParams,
    ) -> CursorPaginatedResponse[SpmFindingRead]:
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == self.organization_id
        )
        if params.endpoint_id is not None:
            stmt = stmt.where(SpmFinding.endpoint_id == params.endpoint_id)
        if params.control_id is not None:
            control_id = _control_id_for_ref(params.control_id)
            stmt = (
                stmt.where(SpmFinding.control_id == control_id)
                if control_id is not None
                else stmt.where(sa.false())
            )
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmFinding,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmFinding.updated_at.desc(), SpmFinding.id.desc())
        stmt = stmt.limit(params.limit + 1)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmFindingRead](
            items=SpmFindingRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_finding(self, finding_id: uuid.UUID) -> SpmFindingRead:
        row = await self._get_finding_row(finding_id)
        return SpmFindingRead.model_validate(row)

    async def create_finding_decision(
        self,
        finding_id: uuid.UUID,
        params: SpmFindingDecisionCreate,
    ) -> SpmFindingDecisionRead:
        finding = await self._get_finding_row(finding_id)
        decision_row = SpmFindingDecision(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            finding_id=finding.id,
            endpoint_id=finding.endpoint_id,
            decision=params.decision.value,
            reason=params.reason,
            payload=params.payload,
            decided_by_user_id=self.role.user_id,
        )
        self.session.add(decision_row)

        now = datetime.now(UTC)
        finding.last_decision_at = now
        finding.closed_at = None
        if params.decision == SpmFindingDecisionType.DISMISS:
            finding.status = SpmFindingStatus.DISMISSED.value
            finding.closed_at = now
        elif params.decision == SpmFindingDecisionType.REOPEN:
            finding.status = SpmFindingStatus.OPEN.value
        elif params.decision == SpmFindingDecisionType.ENFORCE:
            finding.status = SpmFindingStatus.ENFORCEMENT_PENDING.value
            if finding.recommended_action is None:
                raise SpmConflictError(
                    "Finding has no recommended action to enforce.",
                    code="spm_finding_not_enforceable",
                    finding_id=finding_id,
                )
            self.session.add(
                SpmEnforcementTask(
                    id=uuid.uuid4(),
                    organization_id=self.organization_id,
                    endpoint_id=finding.endpoint_id,
                    finding_id=finding.id,
                    action=finding.recommended_action,
                    payload=finding.recommended_payload or {},
                    status=SpmEnforcementTaskStatus.PENDING.value,
                    requested_by_user_id=self.role.user_id,
                )
            )

        await self.session.commit()
        await self.session.refresh(decision_row)
        return SpmFindingDecisionRead.model_validate(decision_row)

    async def create_response_action_preview(
        self,
        finding_id: uuid.UUID,
        params: SpmResponseActionPreviewCreate,
    ) -> SpmResponseActionPreviewRead:
        finding = await self._get_finding_row(finding_id)
        action = params.action or (
            SpmEnforcementAction(finding.recommended_action)
            if finding.recommended_action is not None
            else None
        )
        if action is None:
            raise SpmConflictError(
                "Finding has no response action to preview.",
                code="spm_finding_preview_not_supported",
                finding_id=finding_id,
            )
        if get_response_action(action) is None:
            raise SpmConflictError(
                "Response action does not support previews.",
                code="spm_response_action_preview_not_supported",
                action=action.value,
                finding_id=finding_id,
            )

        payload = {**(finding.recommended_payload or {}), **params.payload}
        now = datetime.now(UTC)
        row = SpmResponseActionPreview(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            endpoint_id=finding.endpoint_id,
            finding_id=finding.id,
            action=action.value,
            payload=payload,
            status=SpmResponseActionPreviewStatus.PENDING.value,
            requested_by_user_id=self.role.user_id,
            expires_at=now + timedelta(minutes=15),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return SpmResponseActionPreviewRead.model_validate(row)

    async def get_response_action_preview(
        self,
        preview_id: uuid.UUID,
    ) -> SpmResponseActionPreviewRead:
        row = await self._get_response_action_preview_row(preview_id)
        self._expire_preview_if_needed(row)
        await self.session.commit()
        await self.session.refresh(row)
        return SpmResponseActionPreviewRead.model_validate(row)

    async def _get_endpoint_row(self, endpoint_id: uuid.UUID) -> SpmEndpoint:
        stmt = select(SpmEndpoint).where(
            SpmEndpoint.id == endpoint_id,
            SpmEndpoint.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise SpmNotFoundError(
                "SPM endpoint not found.",
                code="spm_endpoint_not_found",
                endpoint_id=endpoint_id,
            )
        return row

    async def _get_finding_row(self, finding_id: uuid.UUID) -> SpmFinding:
        stmt = select(SpmFinding).where(
            SpmFinding.id == finding_id,
            SpmFinding.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise SpmNotFoundError(
                "SPM finding not found.",
                code="spm_finding_not_found",
                finding_id=finding_id,
            )
        return row

    async def _get_response_action_preview_row(
        self,
        preview_id: uuid.UUID,
    ) -> SpmResponseActionPreview:
        stmt = select(SpmResponseActionPreview).where(
            SpmResponseActionPreview.id == preview_id,
            SpmResponseActionPreview.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise SpmNotFoundError(
                "SPM response action preview not found.",
                code="spm_response_action_preview_not_found",
                preview_id=preview_id,
            )
        return row

    @staticmethod
    def _expire_preview_if_needed(row: SpmResponseActionPreview) -> None:
        if row.status != SpmResponseActionPreviewStatus.PENDING.value:
            return
        if row.expires_at <= datetime.now(UTC):
            row.status = SpmResponseActionPreviewStatus.EXPIRED.value

    @staticmethod
    def _can_delete_pending_endpoint(endpoint: SpmEndpoint) -> bool:
        return (
            endpoint.status == "pending"
            and endpoint.enrolled_at is None
            and endpoint.last_seen_at is None
            and endpoint.last_sync_at is None
        )


class SpmSyncService:
    """Endpoint-authenticated SPM sync service."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        threat_intel_provider: SpmThreatIntelProvider | None = None,
    ):
        self.session = session
        self.threat_intel_provider = threat_intel_provider

    async def sync_endpoint(
        self,
        *,
        endpoint_id: uuid.UUID,
        bearer_token: str,
        params: SpmEndpointSyncRequest,
    ) -> SpmEndpointSyncResponse:
        endpoint = await self._authenticate_endpoint(
            endpoint_id=endpoint_id,
            bearer_token=bearer_token,
        )
        if not await is_org_entitled(
            self.session, endpoint.organization_id, Entitlement.SPM
        ):
            raise EntitlementRequired(Entitlement.SPM.value)

        issued_secret: str | None = None
        if endpoint.endpoint_secret_hash is None:
            issued_secret = _issue_secret("tcspm_ep")
            endpoint.endpoint_secret_hash = _hash_token(issued_secret)
            endpoint.enrollment_token_hash = None
            endpoint.enrolled_at = datetime.now(UTC)

        endpoint.status = params.status.value
        endpoint.name = params.name or endpoint.name
        endpoint.endpoint_version = params.endpoint_version or endpoint.endpoint_version
        endpoint.hostname = params.hostname or endpoint.hostname
        endpoint.os_user = params.os_user or endpoint.os_user
        endpoint.home_path = params.home_path or endpoint.home_path
        endpoint.client_metadata = params.client_metadata
        endpoint.last_seen_at = datetime.now(UTC)
        endpoint.last_sync_at = endpoint.last_seen_at
        endpoint.last_sync_error = None

        items_by_identity_key: dict[str, SpmInventoryItem] = {}
        for item in params.inventory_items:
            item_row = await self._upsert_inventory_item(endpoint=endpoint, item=item)
            items_by_identity_key[item.identity_key] = item_row
        for relationship in params.relationships:
            await self._upsert_inventory_relationship(
                endpoint=endpoint,
                relationship=relationship,
                items_by_identity_key=items_by_identity_key,
            )
        for task_result in params.task_results:
            await self._apply_task_result(endpoint=endpoint, task_result=task_result)
        for preview_result in params.action_preview_results:
            await self._apply_action_preview_result(
                endpoint=endpoint,
                preview_result=preview_result,
            )

        await self.session.flush()
        threat_intel_provider = self.threat_intel_provider
        if threat_intel_provider is None:
            threat_intel_provider = BestEffortSpmThreatIntelProvider(
                self.session,
                organization_id=endpoint.organization_id,
            )
        await self._analyze_endpoint(
            endpoint,
            threat_intel_provider=threat_intel_provider,
        )
        await self.session.commit()
        await self.session.refresh(endpoint)
        tasks = await self._pending_tasks(endpoint.id, endpoint.organization_id)
        action_previews = await self._pending_action_previews(
            endpoint.id,
            endpoint.organization_id,
        )
        endpoint_read = (
            await _endpoint_reads(
                self.session,
                organization_id=endpoint.organization_id,
                endpoints=[endpoint],
            )
        )[0]
        return SpmEndpointSyncResponse(
            endpoint=endpoint_read,
            endpoint_secret=issued_secret,
            tasks=[SpmEnforcementTaskRead.model_validate(task) for task in tasks],
            action_previews=[
                SpmResponseActionPreviewRead.model_validate(preview)
                for preview in action_previews
            ],
        )

    async def _analyze_endpoint(
        self,
        endpoint: SpmEndpoint,
        *,
        threat_intel_provider: SpmThreatIntelProvider,
    ) -> None:
        policy = SpmControlPolicy.from_client_metadata(endpoint.client_metadata or {})
        rows = await self._endpoint_rows(endpoint)

        handled_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
        failing_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()

        for observation, item in rows:
            controls = _controls_for_item(item)
            if not controls:
                continue

            data = _control_data_from_rows(item=item, observation=observation)
            intelligence = await self._item_intelligence(
                item=item,
                observation=observation,
                threat_intel_provider=threat_intel_provider,
            )
            context = SpmControlContext(
                item=data,
                policy=policy,
                intelligence=intelligence,
            )

            for definition in controls:
                result = definition.check(context)
                control = definition.control
                pair = (item.id, control.id)
                handled_pairs.add(pair)
                if not result.failed:
                    continue

                failing_pairs.add(pair)
                await self._upsert_finding(
                    endpoint=endpoint,
                    item=item,
                    observation=observation,
                    control=control,
                    result=result,
                )

        await self._resolve_passing_findings(
            endpoint=endpoint,
            handled_pairs=handled_pairs,
            failing_pairs=failing_pairs,
        )

    async def _endpoint_rows(
        self, endpoint: SpmEndpoint
    ) -> list[tuple[SpmInventoryObservation, SpmInventoryItem]]:
        stmt = (
            select(SpmInventoryObservation, SpmInventoryItem)
            .join(
                SpmInventoryItem,
                SpmInventoryItem.id == SpmInventoryObservation.inventory_item_id,
            )
            .where(
                SpmInventoryObservation.organization_id == endpoint.organization_id,
                SpmInventoryObservation.endpoint_id == endpoint.id,
            )
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def _item_intelligence(
        self,
        *,
        item: SpmInventoryItem,
        observation: SpmInventoryObservation,
        threat_intel_provider: SpmThreatIntelProvider,
    ) -> dict[str, Any]:
        metadata = item.item_metadata or {}
        evidence = observation.evidence or {}
        if item.item_type == SpmInventoryItemType.MCP_SERVER.value:
            return await threat_intel_provider.enrich_mcp_server(
                metadata=metadata,
                evidence=evidence,
            )
        if item.item_type == SpmInventoryItemType.INSTRUCTION_FILE.value:
            return await threat_intel_provider.enrich_instruction_file(
                metadata=metadata,
                evidence=evidence,
            )
        return {}

    async def _upsert_finding(
        self,
        *,
        endpoint: SpmEndpoint,
        item: SpmInventoryItem,
        observation: SpmInventoryObservation,
        control: SpmControlRead,
        result: SpmControlResult,
    ) -> SpmFinding:
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == endpoint.organization_id,
            SpmFinding.endpoint_id == endpoint.id,
            SpmFinding.inventory_item_id == item.id,
            SpmFinding.control_id == control.id,
        )
        finding = (await self.session.scalars(stmt)).one_or_none()
        now = datetime.now(UTC)
        if finding is None:
            finding = SpmFinding(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                inventory_item_id=item.id,
                inventory_observation_id=observation.id,
                control_id=control.id,
                control_key=control.key,
                control_revision=control.revision,
                harness=control.harness.value,
                item_type=item.item_type,
                source_type=item.source_type,
                item_location=item.item_location,
                source_location=item.source_location,
                severity=control.severity.value,
                status=SpmFindingStatus.OPEN.value,
                summary=result.summary,
                evidence=result.evidence,
                enrichment=result.enrichment,
                recommended_action=control.action.value,
                recommended_payload=result.recommended_payload,
                opened_at=now,
            )
            self.session.add(finding)
            await self.session.flush()
            return finding

        finding.inventory_observation_id = observation.id
        finding.control_key = control.key
        finding.control_revision = control.revision
        finding.harness = control.harness.value
        finding.item_type = item.item_type
        finding.source_type = item.source_type
        finding.item_location = item.item_location
        finding.source_location = item.source_location
        finding.severity = control.severity.value
        finding.summary = result.summary
        finding.evidence = result.evidence
        finding.enrichment = result.enrichment
        finding.recommended_action = control.action.value
        finding.recommended_payload = result.recommended_payload
        finding.closed_at = None

        if finding.status != SpmFindingStatus.ENFORCEMENT_PENDING.value:
            if finding.status != SpmFindingStatus.OPEN.value:
                finding.opened_at = now
            finding.status = SpmFindingStatus.OPEN.value
        return finding

    async def _resolve_passing_findings(
        self,
        *,
        endpoint: SpmEndpoint,
        handled_pairs: set[tuple[uuid.UUID, uuid.UUID]],
        failing_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    ) -> None:
        if not handled_pairs:
            return
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == endpoint.organization_id,
            SpmFinding.endpoint_id == endpoint.id,
        )
        findings = list((await self.session.scalars(stmt)).all())
        now = datetime.now(UTC)
        for finding in findings:
            pair = (finding.inventory_item_id, finding.control_id)
            if pair not in handled_pairs or pair in failing_pairs:
                continue
            if finding.status == SpmFindingStatus.DISMISSED.value:
                continue
            if finding.status != SpmFindingStatus.RESOLVED.value:
                finding.status = SpmFindingStatus.RESOLVED.value
                finding.closed_at = now

    async def _authenticate_endpoint(
        self,
        *,
        endpoint_id: uuid.UUID,
        bearer_token: str,
    ) -> SpmEndpoint:
        stmt = select(SpmEndpoint).where(SpmEndpoint.id == endpoint_id)
        endpoint = (await self.session.scalars(stmt)).one_or_none()
        if endpoint is None:
            raise SpmNotFoundError(
                "SPM endpoint not found.",
                code="spm_endpoint_not_found",
                endpoint_id=endpoint_id,
            )

        supplied_hash = _hash_token(bearer_token)
        expected_hash = endpoint.endpoint_secret_hash or endpoint.enrollment_token_hash
        if expected_hash is None or not secrets.compare_digest(
            supplied_hash, expected_hash
        ):
            raise SpmAuthenticationError(
                "Invalid endpoint token.",
                code="spm_endpoint_token_invalid",
                endpoint_id=endpoint_id,
            )
        return endpoint

    async def _upsert_inventory_item(
        self,
        *,
        endpoint: SpmEndpoint,
        item: SpmSyncInventoryItemUpsert,
    ) -> SpmInventoryItem:
        stmt = select(SpmInventoryItem).where(
            SpmInventoryItem.organization_id == endpoint.organization_id,
            SpmInventoryItem.harness == item.harness.value,
            SpmInventoryItem.item_type == item.item_type.value,
            SpmInventoryItem.source_type == item.source_type.value,
            SpmInventoryItem.item_location == item.item_location,
            SpmInventoryItem.source_location == item.source_location,
            SpmInventoryItem.identity_key == item.identity_key,
        )
        now = datetime.now(UTC)
        item_row = (await self.session.scalars(stmt)).one_or_none()
        if item_row is None:
            item_row = SpmInventoryItem(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                harness=item.harness.value,
                item_type=item.item_type.value,
                source_type=item.source_type.value,
                item_location=item.item_location,
                source_location=item.source_location,
                identity_key=item.identity_key,
                display_name=item.display_name,
                content_hash=item.content_hash,
                item_metadata=item.metadata,
                first_seen_at=now,
                last_seen_at=now,
            )
            # Multiple endpoints can discover the same org-scoped item on their
            # first sync. Use a savepoint so a duplicate insert can fall back to
            # the row that another transaction just committed.
            try:
                async with self.session.begin_nested():
                    self.session.add(item_row)
                    await self.session.flush()
            except IntegrityError:
                item_row = (await self.session.scalars(stmt)).one()

        item_row.display_name = item.display_name
        item_row.content_hash = item.content_hash
        item_row.item_metadata = item.metadata
        item_row.last_seen_at = now

        observation_stmt = select(SpmInventoryObservation).where(
            SpmInventoryObservation.organization_id == endpoint.organization_id,
            SpmInventoryObservation.endpoint_id == endpoint.id,
            SpmInventoryObservation.inventory_item_id == item_row.id,
        )
        observation = (await self.session.scalars(observation_stmt)).one_or_none()
        if observation is None:
            observation = SpmInventoryObservation(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                inventory_item_id=item_row.id,
                workspace_id=item.workspace_id,
                evidence=item.evidence,
                observed_state=item.observed_state,
                content_hash=item.content_hash,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(observation)
        else:
            observation.workspace_id = item.workspace_id
            observation.evidence = item.evidence
            observation.observed_state = item.observed_state
            observation.content_hash = item.content_hash
            observation.last_seen_at = now
        return item_row

    async def _upsert_inventory_relationship(
        self,
        *,
        endpoint: SpmEndpoint,
        relationship: SpmSyncInventoryRelationshipUpsert,
        items_by_identity_key: dict[str, SpmInventoryItem],
    ) -> None:
        from_item = items_by_identity_key.get(relationship.from_identity_key)
        to_item = items_by_identity_key.get(relationship.to_identity_key)
        if from_item is None or to_item is None:
            raise SpmNotFoundError(
                "SPM inventory relationship references an item not in this sync.",
                code="spm_inventory_relationship_item_not_found",
                endpoint_id=endpoint.id,
                from_identity_key=relationship.from_identity_key,
                to_identity_key=relationship.to_identity_key,
            )

        stmt = select(SpmInventoryRelationship).where(
            SpmInventoryRelationship.organization_id == endpoint.organization_id,
            SpmInventoryRelationship.endpoint_id == endpoint.id,
            SpmInventoryRelationship.relationship_type
            == relationship.relationship_type.value,
            SpmInventoryRelationship.from_inventory_item_id == from_item.id,
            SpmInventoryRelationship.to_inventory_item_id == to_item.id,
        )
        now = datetime.now(UTC)
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            row = SpmInventoryRelationship(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                relationship_type=relationship.relationship_type.value,
                from_inventory_item_id=from_item.id,
                to_inventory_item_id=to_item.id,
                evidence=relationship.evidence,
                observed_state=relationship.observed_state,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(row)
            return

        row.evidence = relationship.evidence
        row.observed_state = relationship.observed_state
        row.last_seen_at = now

    async def _apply_task_result(
        self,
        *,
        endpoint: SpmEndpoint,
        task_result: SpmSyncTaskResult,
    ) -> None:
        stmt = select(SpmEnforcementTask).where(
            SpmEnforcementTask.id == task_result.task_id,
            SpmEnforcementTask.organization_id == endpoint.organization_id,
            SpmEnforcementTask.endpoint_id == endpoint.id,
        )
        task = (await self.session.scalars(stmt)).one_or_none()
        if task is None:
            raise SpmNotFoundError(
                "SPM enforcement task not found.",
                code="spm_enforcement_task_not_found",
                endpoint_id=endpoint.id,
                task_id=task_result.task_id,
            )
        task.status = task_result.status.value
        task.result = task_result.result
        task.error = task_result.error
        task.completed_at = task_result.completed_at

        if task.finding_id is not None:
            finding_stmt = select(SpmFinding).where(
                SpmFinding.id == task.finding_id,
                SpmFinding.organization_id == endpoint.organization_id,
            )
            finding = (await self.session.scalars(finding_stmt)).one_or_none()
            if finding is not None:
                if task_result.status == SpmSyncTaskResultStatus.APPLIED:
                    finding.status = SpmFindingStatus.ENFORCED.value
                    finding.closed_at = task_result.completed_at
                elif task_result.status == SpmSyncTaskResultStatus.FAILED:
                    finding.status = SpmFindingStatus.OPEN.value
                    finding.closed_at = None

    async def _apply_action_preview_result(
        self,
        *,
        endpoint: SpmEndpoint,
        preview_result: SpmSyncResponseActionPreviewResult,
    ) -> None:
        stmt = select(SpmResponseActionPreview).where(
            SpmResponseActionPreview.id == preview_result.preview_id,
            SpmResponseActionPreview.organization_id == endpoint.organization_id,
            SpmResponseActionPreview.endpoint_id == endpoint.id,
        )
        preview = (await self.session.scalars(stmt)).one_or_none()
        if preview is None:
            raise SpmNotFoundError(
                "SPM response action preview not found.",
                code="spm_response_action_preview_not_found",
                endpoint_id=endpoint.id,
                preview_id=preview_result.preview_id,
            )
        preview.status = preview_result.status.value
        preview.target_path = preview_result.target_path
        preview.before_content = preview_result.before_content
        preview.after_content = preview_result.after_content
        preview.result = preview_result.result
        preview.error = preview_result.error
        preview.completed_at = preview_result.completed_at

    async def _pending_tasks(
        self,
        endpoint_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> list[SpmEnforcementTask]:
        stmt = (
            select(SpmEnforcementTask)
            .where(
                SpmEnforcementTask.organization_id == organization_id,
                SpmEnforcementTask.endpoint_id == endpoint_id,
                SpmEnforcementTask.status == SpmEnforcementTaskStatus.PENDING.value,
            )
            .order_by(SpmEnforcementTask.created_at.asc(), SpmEnforcementTask.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def _pending_action_previews(
        self,
        endpoint_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> list[SpmResponseActionPreview]:
        now = datetime.now(UTC)
        expired_stmt = (
            sa.update(SpmResponseActionPreview)
            .where(
                SpmResponseActionPreview.organization_id == organization_id,
                SpmResponseActionPreview.endpoint_id == endpoint_id,
                SpmResponseActionPreview.status
                == SpmResponseActionPreviewStatus.PENDING.value,
                SpmResponseActionPreview.expires_at <= now,
            )
            .values(status=SpmResponseActionPreviewStatus.EXPIRED.value)
        )
        await self.session.execute(expired_stmt)

        stmt = (
            select(SpmResponseActionPreview)
            .where(
                SpmResponseActionPreview.organization_id == organization_id,
                SpmResponseActionPreview.endpoint_id == endpoint_id,
                SpmResponseActionPreview.status
                == SpmResponseActionPreviewStatus.PENDING.value,
                SpmResponseActionPreview.expires_at > now,
            )
            .order_by(
                SpmResponseActionPreview.created_at.asc(),
                SpmResponseActionPreview.id.asc(),
            )
        )
        return list((await self.session.scalars(stmt)).all())
