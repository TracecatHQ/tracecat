"""Transactional storage primitives; publication and execution remain opt-in.

Callers own the transaction. No method commits, discovers dependencies, reads
blobs, or treats a persisted reference as authorization.
"""

from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from tracecat.agent.references.contracts import Digest, LogicalPath, SchemaVersion
from tracecat.agent.references.storage_types import ReferencePosition
from tracecat.agent.references.uri import (
    ReferenceKind,
    ReferenceTarget,
    serialize_reference_uri,
)
from tracecat.db.models import (
    AgentPresetVersion,
    AgentPresetVersionReference,
    AgentReferenceRunSnapshot,
    SkillVersion,
    SkillVersionReference,
)
from tracecat.service import BaseWorkspaceService

NonemptyString = Annotated[str, StringConstraints(min_length=1)]
BackendName = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class ReferenceStorageConflict(ValueError):
    """A logical turn already selected different inputs or immutable contents."""


class ReferenceSourceNotFound(ValueError):
    """The source does not exist in the service's workspace."""


class StoredPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    line: int = Field(ge=1, strict=True)
    column: int = Field(ge=1, strict=True)


class DirectReferenceProjection(BaseModel):
    """One direct target in one source file, grouped across its occurrences."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_path: Annotated[LogicalPath, StringConstraints(max_length=1024)]
    target: ReferenceTarget
    occurrences: tuple[StoredPosition, ...] = Field(min_length=1)
    source_sha256: Digest


class SnapshotSelection(BaseModel):
    """Identity that must agree across every attempt of one logical turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    logical_turn_id: UUID
    session_id: UUID
    backend_id: BackendName
    harness_type: BackendName
    input_hash: Digest
    schema_version: SchemaVersion = 1


class SnapshotObject(BaseModel):
    """An already persisted immutable object; storage does not fetch its bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: NonemptyString
    sha256: Digest


class ReferenceStorageService(BaseWorkspaceService):
    service_name = "reference_storage"

    async def replace_skill_projection(
        self, version_id: UUID, references: Sequence[DirectReferenceProjection]
    ) -> None:
        """Replace all direct references and the marker in the caller transaction."""
        await self._replace_projection("skill", version_id, references)

    async def replace_preset_projection(
        self, version_id: UUID, references: Sequence[DirectReferenceProjection]
    ) -> None:
        """Replace instruction references without changing manual attachments."""
        if any(ref.source_path != "instructions.md" for ref in references):
            raise ValueError("Agent references must come from instructions.md")
        await self._replace_projection("agent", version_id, references)

    async def _replace_projection(
        self,
        source_kind: Literal["skill", "agent"],
        version_id: UUID,
        references: Sequence[DirectReferenceProjection],
    ) -> None:
        # Serialize replacement against this immutable published source. The
        # savepoint preserves the previous projection if a later insert fails.
        async with self.session.begin_nested():
            source_model = (
                SkillVersion if source_kind == "skill" else AgentPresetVersion
            )
            source = await self.session.scalar(
                select(source_model)
                .where(
                    source_model.workspace_id == self.workspace_id,
                    source_model.id == version_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if source is None:
                raise ReferenceSourceNotFound("Published source not found")
            if source.reference_schema_version not in (None, 1):
                raise ReferenceStorageConflict("Unsupported projection schema version")
            model = (
                SkillVersionReference
                if source_kind == "skill"
                else AgentPresetVersionReference
            )
            source_column = (
                SkillVersionReference.skill_version_id
                if source_kind == "skill"
                else AgentPresetVersionReference.preset_version_id
            )
            await self.session.execute(
                delete(model).where(
                    model.workspace_id == self.workspace_id, source_column == version_id
                )
            )
            for ref in references:
                target = ref.target
                positions: list[ReferencePosition] = [
                    {"line": pos.line, "column": pos.column} for pos in ref.occurrences
                ]
                row = model(
                    workspace_id=self.workspace_id,
                    source_path=ref.source_path,
                    kind=target.kind.value,
                    target_key=serialize_reference_uri(target),
                    target_id=None
                    if target.kind == ReferenceKind.TOOL
                    else UUID(target.identity),
                    action_key=target.identity
                    if target.kind == ReferenceKind.TOOL
                    else None,
                    tool_name=target.tool_name,
                    occurrences=positions,
                    source_sha256=ref.source_sha256,
                )
                if isinstance(row, SkillVersionReference):
                    row.skill_version_id = version_id
                else:
                    row.preset_version_id = version_id
                self.session.add(row)
            source.reference_schema_version = 1
            await self.session.flush()

    async def select_snapshot(
        self, selection: SnapshotSelection, graph: SnapshotObject
    ) -> AgentReferenceRunSnapshot:
        """Insert a candidate or return the committed winner, never replace it.

        Graph differences are expected for racing resolutions of latest versions.
        Only admitted input/session/backend differences are conflicts. Under
        PostgreSQL READ COMMITTED, a losing insert waits for the winner, and the
        following SELECT sees it. Serialization failures at stronger isolation
        levels must retry the caller's complete transaction.
        """
        await self.session.execute(
            insert(AgentReferenceRunSnapshot)
            .values(
                id=uuid4(),
                workspace_id=self.workspace_id,
                **selection.model_dump(),
                state="resolved",
                graph_object_key=graph.key,
                graph_sha256=graph.sha256,
            )
            .on_conflict_do_nothing(constraint="uq_reference_snapshot_turn")
        )
        snapshot = await self.get_snapshot(selection)
        if snapshot is None:
            raise ReferenceStorageConflict(
                "Snapshot selection disappeared; retry the transaction"
            )
        return snapshot

    async def get_snapshot(
        self, selection: SnapshotSelection
    ) -> AgentReferenceRunSnapshot | None:
        """Read a prior selection under the same original turn identity."""
        snapshot = await self.session.scalar(
            select(AgentReferenceRunSnapshot)
            .where(
                AgentReferenceRunSnapshot.workspace_id == self.workspace_id,
                AgentReferenceRunSnapshot.logical_turn_id == selection.logical_turn_id,
            )
            .execution_options(populate_existing=True)
        )
        if snapshot is not None:
            self._check_selection(snapshot, selection)
        return snapshot

    async def mark_snapshot_ready(
        self, selection: SnapshotSelection, snapshot_id: UUID, bundle: SnapshotObject
    ) -> AgentReferenceRunSnapshot:
        """Publish final semantic readiness once; retries must agree on the bundle."""
        snapshot = await self.session.scalar(
            select(AgentReferenceRunSnapshot)
            .where(
                AgentReferenceRunSnapshot.workspace_id == self.workspace_id,
                AgentReferenceRunSnapshot.logical_turn_id == selection.logical_turn_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if snapshot is None or snapshot.id != snapshot_id:
            raise ReferenceStorageConflict("Selected snapshot not found")
        self._check_selection(snapshot, selection)
        if snapshot.state == "ready":
            if (snapshot.final_object_key, snapshot.final_sha256) != (
                bundle.key,
                bundle.sha256,
            ):
                raise ReferenceStorageConflict(
                    "Snapshot already has a different final bundle"
                )
            return snapshot
        snapshot.state = "ready"
        snapshot.final_object_key = bundle.key
        snapshot.final_sha256 = bundle.sha256
        await self.session.flush()
        return snapshot

    @staticmethod
    def _check_selection(
        snapshot: AgentReferenceRunSnapshot, selection: SnapshotSelection
    ) -> None:
        if (
            snapshot.logical_turn_id,
            snapshot.session_id,
            snapshot.backend_id,
            snapshot.harness_type,
            snapshot.input_hash,
            snapshot.schema_version,
        ) != (
            selection.logical_turn_id,
            selection.session_id,
            selection.backend_id,
            selection.harness_type,
            selection.input_hash,
            selection.schema_version,
        ):
            raise ReferenceStorageConflict(
                "Logical turn already selected different inputs, session, or backend"
            )
