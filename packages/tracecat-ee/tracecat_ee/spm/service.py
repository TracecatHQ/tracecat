"""Services for AI SPM APIs."""

from __future__ import annotations

import hashlib
import importlib.util
import secrets
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    SpmAsset,
    SpmAssetSighting,
    SpmEndpoint,
    SpmEnforcementTask,
    SpmFinding,
    SpmFindingDecision,
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
    SpmAnyControlAssetData,
    SpmAssetQueryParams,
    SpmAssetRead,
    SpmConfigControlData,
    SpmControlContext,
    SpmControlPolicy,
    SpmControlRead,
    SpmControlResult,
    SpmDirectoryControlData,
    SpmEndpointAssetRead,
    SpmEndpointCreate,
    SpmEndpointCreateResponse,
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
    SpmMcpServerControlData,
    SpmSkillControlData,
    SpmSyncAssetUpsert,
    SpmSyncTaskResult,
)
from tracecat_ee.spm.types import (
    SpmAssetType,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
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
    tuple[str, str, str], tuple[SpmControlDefinition, ...]
]:
    grouped: dict[tuple[str, str, str], list[SpmControlDefinition]] = {}
    for definition in _get_control_definitions():
        control = definition.control
        target = (
            control.harness.value,
            control.asset_class.value,
            control.asset_type.value,
        )
        grouped.setdefault(target, []).append(definition)
    return {
        target: tuple(sorted(items, key=lambda definition: definition.control.key))
        for target, items in grouped.items()
    }


def _controls_for_asset(asset: SpmAsset) -> tuple[SpmControlDefinition, ...]:
    return _control_definitions_by_target().get(
        (asset.harness, asset.asset_class, asset.asset_type),
        (),
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
    asset: SpmAsset,
    sighting: SpmAssetSighting,
) -> SpmAnyControlAssetData:
    metadata = asset.asset_metadata or {}
    evidence = sighting.evidence or {}
    observed_state = sighting.observed_state or {}
    base = {
        "id": asset.id,
        "sighting_id": sighting.id,
        "identity_key": asset.identity_key,
        "display_name": asset.display_name,
        "harness": asset.harness,
        "asset_class": asset.asset_class,
        "asset_type": asset.asset_type,
        "content_hash": sighting.content_hash or asset.content_hash,
        "metadata": metadata,
        "evidence": evidence,
        "observed_state": observed_state,
    }

    match asset.asset_type:
        case (
            SpmAssetType.TRUSTED_DIRECTORY.value
            | SpmAssetType.ADDITIONAL_DIRECTORY.value
        ):
            directory_path = (
                _string(metadata.get("directory_path")) or asset.identity_key
            )
            return SpmDirectoryControlData.model_validate(
                {
                    **base,
                    "directory_path": directory_path,
                    "file_path": _string(metadata.get("file_path")),
                    "parse_status": _string(metadata.get("parse_status")),
                }
            )
        case SpmAssetType.PERMISSION_CONFIG.value | SpmAssetType.SANDBOX_CONFIG.value:
            return SpmConfigControlData.model_validate(
                {
                    **base,
                    "file_path": _string(metadata.get("file_path")),
                    "project_root": _string(metadata.get("project_root")),
                    "parse_status": _string(metadata.get("parse_status")),
                    "value": observed_state.get("value"),
                }
            )
        case SpmAssetType.MCP_SERVER.value:
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
        case SpmAssetType.HOOK.value:
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
        case SpmAssetType.SKILL.value:
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
        case SpmAssetType.CLAUDE_MD.value:
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

    raise ValueError(f"Unsupported SPM asset type for controls: {asset.asset_type}")


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


class SpmService(BaseOrgService):
    """Org-scoped service for SPM operator APIs."""

    service_name = "spm"

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
            items=SpmEndpointRead.list_adapter().validate_python(rows),
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
            endpoint=SpmEndpointRead.model_validate(row),
            enrollment_token=enrollment_token,
        )

    async def get_endpoint(self, endpoint_id: uuid.UUID) -> SpmEndpointRead:
        row = await self._get_endpoint_row(endpoint_id)
        return SpmEndpointRead.model_validate(row)

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

    async def list_assets(
        self,
        params: SpmAssetQueryParams,
    ) -> CursorPaginatedResponse[SpmAssetRead]:
        stmt = select(SpmAsset).where(SpmAsset.organization_id == self.organization_id)
        if params.harness is not None:
            stmt = stmt.where(SpmAsset.harness == params.harness.value)
        if params.asset_class is not None:
            stmt = stmt.where(SpmAsset.asset_class == params.asset_class.value)
        if params.asset_type is not None:
            stmt = stmt.where(SpmAsset.asset_type == params.asset_type.value)
        if params.endpoint_id is not None:
            asset_sighting_exists = (
                select(SpmAssetSighting.id)
                .where(
                    SpmAssetSighting.organization_id == self.organization_id,
                    SpmAssetSighting.endpoint_id == params.endpoint_id,
                    SpmAssetSighting.asset_id == SpmAsset.id,
                )
                .exists()
            )
            stmt = stmt.where(asset_sighting_exists)
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmAsset,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmAsset.updated_at.desc(), SpmAsset.id.desc())
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

        return CursorPaginatedResponse[SpmAssetRead](
            items=SpmAssetRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_endpoint_assets(
        self,
        endpoint_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmEndpointAssetRead]:
        await self._get_endpoint_row(endpoint_id)
        stmt = (
            select(
                SpmAsset.id.label("asset_id"),
                SpmAssetSighting.id.label("asset_sighting_id"),
                SpmAsset.organization_id.label("organization_id"),
                SpmAssetSighting.endpoint_id.label("endpoint_id"),
                SpmAssetSighting.workspace_id.label("workspace_id"),
                SpmAsset.harness.label("harness"),
                SpmAsset.asset_class.label("asset_class"),
                SpmAsset.asset_type.label("asset_type"),
                SpmAsset.identity_key.label("identity_key"),
                SpmAsset.display_name.label("display_name"),
                SpmAssetSighting.content_hash.label("content_hash"),
                SpmAsset.asset_metadata.label("asset_metadata"),
                SpmAssetSighting.evidence.label("evidence"),
                SpmAssetSighting.observed_state.label("observed_state"),
                SpmAssetSighting.first_seen_at.label("first_seen_at"),
                SpmAssetSighting.last_seen_at.label("last_seen_at"),
            )
            .select_from(SpmAssetSighting)
            .join(SpmAsset, SpmAsset.id == SpmAssetSighting.asset_id)
            .where(
                SpmAssetSighting.organization_id == self.organization_id,
                SpmAssetSighting.endpoint_id == endpoint_id,
            )
        )
        stmt = _apply_desc_cursor_filter_columns(
            stmt,
            cursor=params.cursor,
            sort_column=SpmAssetSighting.last_seen_at,
            id_column=SpmAssetSighting.id,
        )
        stmt = stmt.order_by(
            SpmAssetSighting.last_seen_at.desc(),
            SpmAssetSighting.id.desc(),
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
                id=last["asset_sighting_id"],
                sort_column="last_seen_at",
                sort_value=last["last_seen_at"],
            )

        return CursorPaginatedResponse[SpmEndpointAssetRead](
            items=SpmEndpointAssetRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_asset(self, asset_id: uuid.UUID) -> SpmAssetRead:
        stmt = select(SpmAsset).where(
            SpmAsset.id == asset_id,
            SpmAsset.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise SpmNotFoundError(
                "SPM asset not found.",
                code="spm_asset_not_found",
                asset_id=asset_id,
            )
        return SpmAssetRead.model_validate(row)

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

        for asset in params.assets:
            await self._upsert_asset(endpoint=endpoint, asset=asset)
        for task_result in params.task_results:
            await self._apply_task_result(endpoint=endpoint, task_result=task_result)

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
        return SpmEndpointSyncResponse(
            endpoint=SpmEndpointRead.model_validate(endpoint),
            endpoint_secret=issued_secret,
            tasks=[SpmEnforcementTaskRead.model_validate(task) for task in tasks],
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

        for sighting, asset in rows:
            controls = _controls_for_asset(asset)
            if not controls:
                continue

            data = _control_data_from_rows(asset=asset, sighting=sighting)
            intelligence = await self._asset_intelligence(
                asset=asset,
                sighting=sighting,
                threat_intel_provider=threat_intel_provider,
            )
            context = SpmControlContext(
                asset=data,
                policy=policy,
                intelligence=intelligence,
            )

            for definition in controls:
                result = definition.check(context)
                control = definition.control
                pair = (asset.id, control.id)
                handled_pairs.add(pair)
                if not result.failed:
                    continue

                failing_pairs.add(pair)
                await self._upsert_finding(
                    endpoint=endpoint,
                    asset=asset,
                    sighting=sighting,
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
    ) -> list[tuple[SpmAssetSighting, SpmAsset]]:
        stmt = (
            select(SpmAssetSighting, SpmAsset)
            .join(SpmAsset, SpmAsset.id == SpmAssetSighting.asset_id)
            .where(
                SpmAssetSighting.organization_id == endpoint.organization_id,
                SpmAssetSighting.endpoint_id == endpoint.id,
            )
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def _asset_intelligence(
        self,
        *,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        threat_intel_provider: SpmThreatIntelProvider,
    ) -> dict[str, Any]:
        metadata = asset.asset_metadata or {}
        evidence = sighting.evidence or {}
        if asset.asset_type == SpmAssetType.MCP_SERVER.value:
            return await threat_intel_provider.enrich_mcp_server(
                metadata=metadata,
                evidence=evidence,
            )
        if asset.asset_type == SpmAssetType.CLAUDE_MD.value:
            return await threat_intel_provider.enrich_instruction_file(
                metadata=metadata,
                evidence=evidence,
            )
        return {}

    async def _upsert_finding(
        self,
        *,
        endpoint: SpmEndpoint,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        control: SpmControlRead,
        result: SpmControlResult,
    ) -> SpmFinding:
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == endpoint.organization_id,
            SpmFinding.endpoint_id == endpoint.id,
            SpmFinding.asset_id == asset.id,
            SpmFinding.control_id == control.id,
        )
        finding = (await self.session.scalars(stmt)).one_or_none()
        now = datetime.now(UTC)
        if finding is None:
            finding = SpmFinding(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                asset_id=asset.id,
                asset_sighting_id=sighting.id,
                control_id=control.id,
                control_key=control.key,
                control_revision=control.revision,
                harness=control.harness.value,
                asset_class=control.asset_class.value,
                asset_type=control.asset_type.value,
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

        finding.asset_sighting_id = sighting.id
        finding.control_key = control.key
        finding.control_revision = control.revision
        finding.harness = control.harness.value
        finding.asset_class = control.asset_class.value
        finding.asset_type = control.asset_type.value
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
            pair = (finding.asset_id, finding.control_id)
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

    async def _upsert_asset(
        self,
        *,
        endpoint: SpmEndpoint,
        asset: SpmSyncAssetUpsert,
    ) -> None:
        stmt = select(SpmAsset).where(
            SpmAsset.organization_id == endpoint.organization_id,
            SpmAsset.harness == asset.harness.value,
            SpmAsset.asset_class == asset.asset_class.value,
            SpmAsset.asset_type == asset.asset_type.value,
            SpmAsset.identity_key == asset.identity_key,
        )
        now = datetime.now(UTC)
        asset_row = (await self.session.scalars(stmt)).one_or_none()
        if asset_row is None:
            asset_row = SpmAsset(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                harness=asset.harness.value,
                asset_class=asset.asset_class.value,
                asset_type=asset.asset_type.value,
                identity_key=asset.identity_key,
                display_name=asset.display_name,
                content_hash=asset.content_hash,
                asset_metadata=asset.metadata,
                first_seen_at=now,
                last_seen_at=now,
            )
            # Multiple endpoints can discover the same org-scoped asset on their
            # first sync. Use a savepoint so a duplicate insert can fall back to
            # the row that another transaction just committed.
            try:
                async with self.session.begin_nested():
                    self.session.add(asset_row)
                    await self.session.flush()
            except IntegrityError:
                asset_row = (await self.session.scalars(stmt)).one()

        asset_row.display_name = asset.display_name
        asset_row.content_hash = asset.content_hash
        asset_row.asset_metadata = asset.metadata
        asset_row.last_seen_at = now

        sighting_stmt = select(SpmAssetSighting).where(
            SpmAssetSighting.organization_id == endpoint.organization_id,
            SpmAssetSighting.endpoint_id == endpoint.id,
            SpmAssetSighting.asset_id == asset_row.id,
        )
        sighting = (await self.session.scalars(sighting_stmt)).one_or_none()
        if sighting is None:
            sighting = SpmAssetSighting(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                asset_id=asset_row.id,
                workspace_id=asset.workspace_id,
                evidence=asset.evidence,
                observed_state=asset.observed_state,
                content_hash=asset.content_hash,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(sighting)
        else:
            sighting.workspace_id = asset.workspace_id
            sighting.evidence = asset.evidence
            sighting.observed_state = asset.observed_state
            sighting.content_hash = asset.content_hash
            sighting.last_seen_at = now

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
