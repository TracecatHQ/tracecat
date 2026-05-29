from __future__ import annotations

import uuid
from importlib.metadata import entry_points

import orjson
import pytest
from tracecat_ee.agent.artifacts.mount_only_provider import (
    MountOnlyArtifactWorkingSetProvider,
)

from tracecat.agent.artifacts.hydration import (
    ArtifactHydrationContext,
    ArtifactHydratorRegistry,
    MountedArtifactContent,
)
from tracecat.agent.artifacts.providers import (
    ARTIFACT_PROVIDER_ENTRY_POINT_GROUP,
    ARTIFACT_PROVIDER_ENV,
    NoopArtifactWorkingSetProvider,
    get_artifact_working_set_provider,
)
from tracecat.agent.artifacts.working_set import ArtifactWorkingSetContext
from tracecat.artifacts.schemas import Artifact, CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus


def _role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        service_id="tracecat-agent-executor",
    )


@pytest.mark.anyio
async def test_noop_artifact_working_set_provider_writes_nothing(tmp_path) -> None:
    provider = NoopArtifactWorkingSetProvider()
    workspace_id = uuid.uuid4()

    result = await provider.prepare_turn(
        ArtifactWorkingSetContext(
            session_id=uuid.uuid4(),
            workspace_id=workspace_id,
            role=_role(workspace_id),
            artifacts=[],
            host_work_dir=tmp_path / "host",
            runtime_work_dir=tmp_path / "runtime",
        )
    )

    assert result.manifest.artifacts == []
    assert result.prompt_fragment is None
    assert not (tmp_path / "host" / ".tracecat").exists()


def test_ee_artifact_provider_entry_point_is_registered() -> None:
    artifact_entry_points = {
        entry_point.name: entry_point
        for entry_point in entry_points(group=ARTIFACT_PROVIDER_ENTRY_POINT_GROUP)
    }

    entry_point = artifact_entry_points["workspace_chat"]
    assert (
        entry_point.value
        == "tracecat_ee.agent.artifacts.mount_only_provider:build_provider"
    )
    provider = entry_point.load()()
    assert isinstance(provider, MountOnlyArtifactWorkingSetProvider)


def test_provider_resolution_uses_configured_import_path(monkeypatch) -> None:
    get_artifact_working_set_provider.cache_clear()
    monkeypatch.setenv(
        ARTIFACT_PROVIDER_ENV,
        "tracecat_ee.agent.artifacts.mount_only_provider:build_provider",
    )

    try:
        provider = get_artifact_working_set_provider()
    finally:
        get_artifact_working_set_provider.cache_clear()

    assert isinstance(provider, MountOnlyArtifactWorkingSetProvider)


@pytest.mark.anyio
async def test_mount_only_provider_writes_manifest_and_artifact_files(tmp_path) -> None:
    class TestCaseHydrator:
        async def hydrate(
            self,
            artifact: Artifact,
            ctx: ArtifactHydrationContext,
        ) -> MountedArtifactContent | None:
            _ = ctx
            if not isinstance(artifact, CaseArtifact):
                return None
            return MountedArtifactContent(
                filename="case.json",
                content_type="case.read",
                payload={
                    "id": artifact.id,
                    "summary": artifact.title,
                    "description": "Full hydrated case body",
                    "status": artifact.status,
                    "severity": artifact.severity,
                    "fields": [{"id": "customer", "name": "Customer", "value": "Acme"}],
                },
            )

    provider = MountOnlyArtifactWorkingSetProvider(
        hydrators=ArtifactHydratorRegistry({"case": TestCaseHydrator()})
    )
    workspace_id = uuid.uuid4()
    artifact = CaseArtifact(
        id="case_123",
        title="Suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )

    result = await provider.prepare_turn(
        ArtifactWorkingSetContext(
            session_id=uuid.uuid4(),
            workspace_id=workspace_id,
            role=_role(workspace_id),
            artifacts=[artifact],
            host_work_dir=tmp_path / "host",
            runtime_work_dir=tmp_path / "runtime",
        )
    )

    manifest_path = tmp_path / "host" / ".tracecat" / "artifacts" / "manifest.json"
    artifact_path = (
        tmp_path
        / "host"
        / ".tracecat"
        / "artifacts"
        / "case"
        / "case_123"
        / "artifact.json"
    )
    case_path = (
        tmp_path
        / "host"
        / ".tracecat"
        / "artifacts"
        / "case"
        / "case_123"
        / "case.json"
    )
    manifest = orjson.loads(manifest_path.read_bytes())
    artifact_payload = orjson.loads(artifact_path.read_bytes())
    case_payload = orjson.loads(case_path.read_bytes())

    assert result.manifest.commit_available is False
    assert result.prompt_fragment is not None
    assert manifest["commit_available"] is False
    assert manifest["active_artifact_id"] == "case:case_123"
    assert manifest["artifacts"] == [
        {
            "artifact_id": "case:case_123",
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "path": str(
                tmp_path
                / "runtime"
                / ".tracecat"
                / "artifacts"
                / "case"
                / "case_123"
                / "case.json"
            ),
            "capabilities": ["read", "scratch_edit"],
            "metadata": {
                "hydrated": True,
                "projection_path": str(
                    tmp_path
                    / "runtime"
                    / ".tracecat"
                    / "artifacts"
                    / "case"
                    / "case_123"
                    / "artifact.json"
                ),
                "content_type": "case.read",
            },
        }
    ]
    assert artifact_payload == {
        "type": "case",
        "id": "case_123",
        "title": "Suspicious login",
        "severity": "high",
        "status": "new",
    }
    assert case_payload == {
        "id": "case_123",
        "summary": "Suspicious login",
        "description": "Full hydrated case body",
        "status": "new",
        "severity": "high",
        "fields": [{"id": "customer", "name": "Customer", "value": "Acme"}],
    }
