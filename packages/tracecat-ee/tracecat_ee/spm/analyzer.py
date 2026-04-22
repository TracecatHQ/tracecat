"""Inline and external analysis for persisted SPM inventory."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import SpmAsset, SpmAssetSighting, SpmEndpoint, SpmFinding
from tracecat_ee.spm.controls import get_control
from tracecat_ee.spm.schemas import SpmControlRead
from tracecat_ee.spm.types import SpmFindingStatus


class SpmExternalEnricher(Protocol):
    """Asynchronous network-bound enricher interface."""

    async def enrich_findings(self, finding_ids: list[uuid.UUID]) -> None: ...


class NoopSpmExternalEnricher:
    """Default external enricher implementation."""

    async def enrich_findings(self, finding_ids: list[uuid.UUID]) -> None:
        _ = finding_ids


@dataclass(slots=True)
class SpmPolicy:
    """Best-effort policy materialization from endpoint metadata."""

    approved_mcp_servers: set[str] = field(default_factory=set)
    approved_trusted_directories: set[str] = field(default_factory=set)
    approved_additional_directories: set[str] = field(default_factory=set)
    approved_hooks: set[str] = field(default_factory=set)
    approved_skills: set[str] = field(default_factory=set)
    approved_permission_config: Any = None
    approved_sandbox_config: Any = None

    @classmethod
    def from_client_metadata(cls, client_metadata: dict[str, Any]) -> SpmPolicy:
        raw_policy = client_metadata.get("spm_policy")
        if not isinstance(raw_policy, dict):
            return cls()

        return cls(
            approved_mcp_servers={
                identity
                for identity in (
                    cls._normalize_mcp_identity(item)
                    for item in raw_policy.get("approved_mcp_servers", [])
                )
                if identity is not None
            },
            approved_trusted_directories=set(
                cls._string_items(raw_policy.get("approved_trusted_directories", []))
            ),
            approved_additional_directories=set(
                cls._string_items(raw_policy.get("approved_additional_directories", []))
            ),
            approved_hooks=set(cls._string_items(raw_policy.get("approved_hooks", []))),
            approved_skills=set(
                cls._string_items(raw_policy.get("approved_skills", []))
            ),
            approved_permission_config=raw_policy.get("approved_permission_config"),
            approved_sandbox_config=raw_policy.get("approved_sandbox_config"),
        )

    @staticmethod
    def _string_items(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str) and item]

    @staticmethod
    def _normalize_mcp_identity(raw: Any) -> str | None:
        if isinstance(raw, str) and raw:
            return raw
        if not isinstance(raw, dict):
            return None
        server_name = raw.get("server_name")
        resolved_identity = raw.get("resolved_identity")
        if not isinstance(server_name, str) or not isinstance(resolved_identity, str):
            return None
        return f"{server_name}|{resolved_identity}"


@dataclass(slots=True)
class EvaluationResult:
    """Analyzer outcome for a single control against an asset."""

    control: SpmControlRead
    failed: bool
    summary: str
    evidence: dict[str, Any]
    recommended_payload: dict[str, Any]
    schedule_external_enrichment: bool = False


class SpmInventoryAnalyzer:
    """Evaluate persisted endpoint inventory into current-state findings."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        external_enricher: SpmExternalEnricher | None = None,
        schedule_background_tasks: bool = True,
    ) -> None:
        self.session = session
        self.external_enricher = external_enricher or NoopSpmExternalEnricher()
        self.schedule_background_tasks = schedule_background_tasks

    async def analyze_endpoint(self, endpoint: SpmEndpoint) -> None:
        policy = SpmPolicy.from_client_metadata(endpoint.client_metadata or {})
        rows = await self._endpoint_rows(endpoint)

        handled_pairs: set[tuple[uuid.UUID, str]] = set()
        failing_pairs: set[tuple[uuid.UUID, str]] = set()
        enrichable_ids: list[uuid.UUID] = []

        for sighting, asset in rows:
            for evaluation in self._evaluate_asset(
                endpoint=endpoint, asset=asset, sighting=sighting, policy=policy
            ):
                pair = (asset.id, evaluation.control.id)
                handled_pairs.add(pair)
                if not evaluation.failed:
                    continue

                failing_pairs.add(pair)
                finding = await self._upsert_finding(
                    endpoint=endpoint,
                    asset=asset,
                    sighting=sighting,
                    evaluation=evaluation,
                )
                if evaluation.schedule_external_enrichment:
                    enrichable_ids.append(finding.id)

        await self._resolve_passing_findings(
            endpoint=endpoint,
            handled_pairs=handled_pairs,
            failing_pairs=failing_pairs,
        )

        if enrichable_ids:
            await self._schedule_external_enrichment(enrichable_ids)

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

    def _evaluate_asset(
        self,
        *,
        endpoint: SpmEndpoint,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        policy: SpmPolicy,
    ) -> list[EvaluationResult]:
        _ = endpoint
        metadata = asset.asset_metadata or {}
        parse_status = metadata.get("parse_status")

        results: list[EvaluationResult] = []

        if asset.asset_type == "trusted_directory":
            results.append(
                self._evaluate_directory_approval(
                    control_id="claude.trusted_directory.approved",
                    asset=asset,
                    approved_paths=policy.approved_trusted_directories,
                    payload={
                        "directory_path": metadata.get(
                            "directory_path", asset.identity_key
                        )
                    },
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "additional_directory":
            results.append(
                self._evaluate_directory_approval(
                    control_id="claude.additional_directory.approved",
                    asset=asset,
                    approved_paths=policy.approved_additional_directories,
                    payload={
                        "directory_path": metadata.get(
                            "directory_path", asset.identity_key
                        )
                    },
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "permission_config":
            results.append(
                self._evaluate_config_approval(
                    control_id="claude.permission_config.approved",
                    asset=asset,
                    observed=sighting.observed_state.get("value"),
                    approved=policy.approved_permission_config,
                    payload={
                        "target_path": metadata.get("file_path"),
                        "value": policy.approved_permission_config,
                    },
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "sandbox_config":
            results.append(
                self._evaluate_config_approval(
                    control_id="claude.sandbox_config.approved",
                    asset=asset,
                    observed=sighting.observed_state.get("value"),
                    approved=policy.approved_sandbox_config,
                    payload={
                        "target_path": metadata.get("file_path"),
                        "value": policy.approved_sandbox_config,
                    },
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "mcp_server":
            results.extend(
                self._evaluate_mcp_server(
                    asset=asset,
                    sighting=sighting,
                    policy=policy,
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "hook":
            results.append(
                self._evaluate_name_approval(
                    control_id="claude.hook.approved",
                    asset=asset,
                    approved=policy.approved_hooks,
                    fingerprint=metadata.get("fingerprint"),
                    payload={"fingerprint": metadata.get("fingerprint")},
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "skill":
            results.append(
                self._evaluate_name_approval(
                    control_id="claude.skill.approved",
                    asset=asset,
                    approved=policy.approved_skills,
                    fingerprint=metadata.get("fingerprint"),
                    payload={"fingerprint": metadata.get("fingerprint")},
                    parse_status=parse_status,
                )
            )
        elif asset.asset_type == "claude_md":
            results.extend(
                self._evaluate_instruction_file(
                    asset=asset, sighting=sighting, parse_status=parse_status
                )
            )

        return results

    def _evaluate_directory_approval(
        self,
        *,
        control_id: str,
        asset: SpmAsset,
        approved_paths: set[str],
        payload: dict[str, Any],
        parse_status: Any,
    ) -> EvaluationResult:
        control = _require_control(control_id)
        path = payload.get("directory_path", asset.identity_key)
        failed = parse_status != "ok" or (
            bool(approved_paths) and path not in approved_paths
        )
        summary = f"{asset.display_name} is not approved"
        if parse_status != "ok":
            summary = (
                f"{asset.display_name} could not be parsed for approval evaluation"
            )
        return EvaluationResult(
            control=control,
            failed=failed,
            summary=summary,
            evidence={"directory_path": path, "parse_status": parse_status},
            recommended_payload=payload,
        )

    def _evaluate_config_approval(
        self,
        *,
        control_id: str,
        asset: SpmAsset,
        observed: Any,
        approved: Any,
        payload: dict[str, Any],
        parse_status: Any,
    ) -> EvaluationResult:
        control = _require_control(control_id)
        failed = parse_status != "ok" or (approved is not None and observed != approved)
        summary = f"{asset.display_name} does not match the approved configuration"
        if parse_status != "ok":
            summary = (
                f"{asset.display_name} could not be parsed for configuration analysis"
            )
        return EvaluationResult(
            control=control,
            failed=failed,
            summary=summary,
            evidence={
                "parse_status": parse_status,
                "observed": observed,
                "approved": approved,
            },
            recommended_payload=payload if approved is not None else {},
        )

    def _evaluate_name_approval(
        self,
        *,
        control_id: str,
        asset: SpmAsset,
        approved: set[str],
        fingerprint: Any,
        payload: dict[str, Any],
        parse_status: Any,
    ) -> EvaluationResult:
        control = _require_control(control_id)
        failed = parse_status != "ok" or (
            bool(approved)
            and isinstance(fingerprint, str)
            and fingerprint not in approved
        )
        summary = f"{asset.display_name} is not approved"
        if parse_status != "ok":
            summary = f"{asset.display_name} could not be parsed for approval analysis"
        return EvaluationResult(
            control=control,
            failed=failed,
            summary=summary,
            evidence={"fingerprint": fingerprint, "parse_status": parse_status},
            recommended_payload=payload,
        )

    def _evaluate_mcp_server(
        self,
        *,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        policy: SpmPolicy,
        parse_status: Any,
    ) -> list[EvaluationResult]:
        metadata = asset.asset_metadata or {}
        approval_identity = metadata.get("mcp_identity_key")
        payload = {
            "server_name": metadata.get("server_name"),
            "resolved_identity": metadata.get("resolved_identity"),
            "source_path": metadata.get("file_path"),
            "project_root": metadata.get("project_root"),
        }

        approval_failed = parse_status != "ok" or (
            bool(policy.approved_mcp_servers)
            and isinstance(approval_identity, str)
            and approval_identity not in policy.approved_mcp_servers
        )
        reputation_status = _reputation_status(metadata, sighting.evidence)
        vulnerability_status = _vulnerability_status(metadata, sighting.evidence)

        return [
            EvaluationResult(
                control=_require_control("claude.mcp_server.approved"),
                failed=approval_failed,
                summary=(
                    f"{asset.display_name} is not approved"
                    if parse_status == "ok"
                    else f"{asset.display_name} could not be parsed for approval analysis"
                ),
                evidence={
                    "approval_identity": approval_identity,
                    "parse_status": parse_status,
                },
                recommended_payload=payload,
            ),
            EvaluationResult(
                control=_require_control("claude.mcp_server.reputation_ok"),
                failed=reputation_status == "bad",
                summary=f"{asset.display_name} has a failing reputation result",
                evidence={
                    "reputation_status": reputation_status,
                    "resolved_identity": metadata.get("resolved_identity"),
                },
                recommended_payload=payload,
                schedule_external_enrichment=bool(metadata.get("resolved_identity")),
            ),
            EvaluationResult(
                control=_require_control("claude.mcp_server.vulnerability_ok"),
                failed=vulnerability_status == "bad",
                summary=f"{asset.display_name} has a vulnerable resolved identity",
                evidence={
                    "vulnerability_status": vulnerability_status,
                    "resolved_identity": metadata.get("resolved_identity"),
                },
                recommended_payload=payload,
                schedule_external_enrichment=bool(metadata.get("resolved_identity")),
            ),
        ]

    def _evaluate_instruction_file(
        self,
        *,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        parse_status: Any,
    ) -> list[EvaluationResult]:
        metadata = asset.asset_metadata or {}
        evidence = sighting.evidence or {}
        language = evidence.get("language_signal", {})
        obfuscation = evidence.get("obfuscation", {})
        urls = evidence.get("urls", [])
        domains = evidence.get("domains", [])
        ips = evidence.get("ips", [])
        indicator_reputation_status = _reputation_status(metadata, evidence)
        payload = {
            "file_path": metadata.get("file_path"),
            "project_root": metadata.get("project_root"),
        }

        return [
            EvaluationResult(
                control=_require_control("claude.instruction_file.language_english"),
                failed=parse_status != "ok" or language.get("likely_english") is False,
                summary=(
                    f"{asset.display_name} is not English-language"
                    if parse_status == "ok"
                    else f"{asset.display_name} could not be parsed for language analysis"
                ),
                evidence={
                    "parse_status": parse_status,
                    "language_signal": language,
                },
                recommended_payload=payload,
            ),
            EvaluationResult(
                control=_require_control("claude.instruction_file.obfuscation_absent"),
                failed=parse_status != "ok"
                or obfuscation.get("obfuscation_detected") is True,
                summary=(
                    f"{asset.display_name} contains obfuscation indicators"
                    if parse_status == "ok"
                    else f"{asset.display_name} could not be parsed for obfuscation analysis"
                ),
                evidence={
                    "parse_status": parse_status,
                    "obfuscation": obfuscation,
                },
                recommended_payload=payload,
            ),
            EvaluationResult(
                control=_require_control(
                    "claude.instruction_file.external_indicators_reputation_ok"
                ),
                failed=indicator_reputation_status == "bad",
                summary=f"{asset.display_name} contains indicators with failing reputation",
                evidence={
                    "urls": urls,
                    "domains": domains,
                    "ips": ips,
                    "indicator_reputation_status": indicator_reputation_status,
                },
                recommended_payload=payload,
                schedule_external_enrichment=bool(urls or domains or ips),
            ),
        ]

    async def _upsert_finding(
        self,
        *,
        endpoint: SpmEndpoint,
        asset: SpmAsset,
        sighting: SpmAssetSighting,
        evaluation: EvaluationResult,
    ) -> SpmFinding:
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == endpoint.organization_id,
            SpmFinding.endpoint_id == endpoint.id,
            SpmFinding.asset_id == asset.id,
            SpmFinding.control_id == evaluation.control.id,
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
                control_id=evaluation.control.id,
                control_revision=evaluation.control.revision,
                harness=evaluation.control.harness.value,
                asset_class=evaluation.control.asset_class.value,
                asset_type=evaluation.control.asset_type.value,
                severity=evaluation.control.severity.value,
                status=SpmFindingStatus.OPEN.value,
                summary=evaluation.summary,
                evidence=evaluation.evidence,
                recommended_action=evaluation.control.action.value,
                recommended_payload=evaluation.recommended_payload,
                opened_at=now,
            )
            self.session.add(finding)
            await self.session.flush()
            return finding

        finding.asset_sighting_id = sighting.id
        finding.control_revision = evaluation.control.revision
        finding.harness = evaluation.control.harness.value
        finding.asset_class = evaluation.control.asset_class.value
        finding.asset_type = evaluation.control.asset_type.value
        finding.severity = evaluation.control.severity.value
        finding.summary = evaluation.summary
        finding.evidence = evaluation.evidence
        finding.recommended_action = evaluation.control.action.value
        finding.recommended_payload = evaluation.recommended_payload
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
        handled_pairs: set[tuple[uuid.UUID, str]],
        failing_pairs: set[tuple[uuid.UUID, str]],
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

    async def _schedule_external_enrichment(self, finding_ids: list[uuid.UUID]) -> None:
        finding_ids = list(dict.fromkeys(finding_ids))
        if not finding_ids:
            return
        if self.schedule_background_tasks:
            try:
                asyncio.get_running_loop().create_task(
                    self.external_enricher.enrich_findings(finding_ids)
                )
                return
            except RuntimeError:
                pass
        await self.external_enricher.enrich_findings(finding_ids)


def _require_control(control_id: str) -> SpmControlRead:
    control = get_control(control_id)
    if control is None:
        raise ValueError(f"Missing control definition: {control_id}")
    return control


def _reputation_status(
    metadata: dict[str, Any], evidence: dict[str, Any]
) -> str | None:
    for source in (metadata, evidence):
        value = source.get("reputation_status")
        if isinstance(value, str):
            return value
        value = source.get("indicator_reputation_status")
        if isinstance(value, str):
            return value
    return None


def _vulnerability_status(
    metadata: dict[str, Any], evidence: dict[str, Any]
) -> str | None:
    for source in (metadata, evidence):
        value = source.get("vulnerability_status")
        if isinstance(value, str):
            return value
    return None
