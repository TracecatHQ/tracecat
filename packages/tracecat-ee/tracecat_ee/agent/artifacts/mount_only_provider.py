"""Mount-only Workspace Chat artifact working-set provider."""

from __future__ import annotations

import html
import re
import shutil
import stat
from pathlib import Path
from typing import Any

import orjson

from tracecat.agent.artifacts.hydration import (
    ArtifactHydrationContext,
    ArtifactHydratorRegistry,
    MountedArtifactContent,
)
from tracecat.agent.artifacts.working_set import (
    ArtifactWorkingSetContext,
    ArtifactWorkingSetEntry,
    ArtifactWorkingSetManifest,
    ArtifactWorkingSetProvider,
    ArtifactWorkingSetResult,
)
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.artifacts.schemas import Artifact
from tracecat.logger import logger
from tracecat_ee.agent.artifacts.hydrators import build_hydrator_registry

_SAFE_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class MountOnlyArtifactWorkingSetProvider:
    """Write read-only artifact working copies into the agent work directory."""

    def __init__(self, hydrators: ArtifactHydratorRegistry | None = None) -> None:
        self._hydrators = hydrators or build_hydrator_registry()

    async def prepare_turn(
        self,
        ctx: ArtifactWorkingSetContext,
    ) -> ArtifactWorkingSetResult:
        """Prepare scratch artifact files for a Workspace Chat turn."""
        host_root = _prepare_host_artifact_root(ctx.host_work_dir)
        runtime_root = ctx.runtime_work_dir / ".tracecat" / "artifacts"

        logger.info(
            "Preparing Workspace Chat artifact working set",
            session_id=str(ctx.session_id),
            workspace_id=str(ctx.workspace_id),
            artifact_count=len(ctx.artifacts),
            host_root=str(host_root),
            runtime_root=str(runtime_root),
        )

        entries = [
            await self._write_artifact_files(
                artifact,
                ctx=ctx,
                host_root=host_root,
                runtime_root=runtime_root,
            )
            for artifact in ctx.artifacts
        ]
        manifest = ArtifactWorkingSetManifest(
            root=str(runtime_root),
            active_artifact_id=entries[-1].artifact_id if entries else None,
            commit_available=False,
            artifacts=entries,
        )
        (host_root / "manifest.json").write_text(
            manifest.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        logger.info(
            "Prepared Workspace Chat artifact manifest",
            session_id=str(ctx.session_id),
            manifest_path=str(runtime_root / "manifest.json"),
            artifact_count=len(entries),
        )
        return ArtifactWorkingSetResult(
            manifest=manifest,
            prompt_fragment=_build_prompt_fragment(manifest) if entries else None,
        )

    def mcp_tools(self) -> list[MCPToolDefinition]:
        """Return no artifact tools for the mount-only tier."""
        return []

    async def _write_artifact_files(
        self,
        artifact: Artifact,
        *,
        ctx: ArtifactWorkingSetContext,
        host_root: Path,
        runtime_root: Path,
    ) -> ArtifactWorkingSetEntry:
        safe_type = _safe_path_segment(artifact.type)
        safe_id = _safe_path_segment(artifact.id)
        artifact_dir = Path(safe_type) / safe_id
        projection_relative_path = artifact_dir / "artifact.json"
        projection_runtime_path = runtime_root / projection_relative_path

        projection_payload = artifact.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        self._write_json(host_root / projection_relative_path, projection_payload)

        primary_relative_path = projection_relative_path
        metadata: dict[str, Any] = {
            "hydrated": False,
            "projection_path": str(projection_runtime_path),
        }

        content = await self._hydrate_artifact(artifact, ctx=ctx)
        if content is not None:
            primary_relative_path = artifact_dir / _safe_path_segment(content.filename)
            self._write_json(host_root / primary_relative_path, content.payload)
            metadata["hydrated"] = True
            metadata["content_type"] = content.content_type

        runtime_path = runtime_root / primary_relative_path
        logger.info(
            "Mounted Workspace Chat artifact file",
            session_id=str(ctx.session_id),
            artifact_type=artifact.type,
            artifact_id=artifact.id,
            title=artifact.title,
            projection_path=str(projection_runtime_path),
            path=str(runtime_path),
            hydrated=metadata["hydrated"],
            content_type=metadata.get("content_type"),
        )

        return ArtifactWorkingSetEntry(
            artifact_id=f"{artifact.type}:{artifact.id}",
            type=artifact.type,
            id=artifact.id,
            title=artifact.title,
            path=str(runtime_path),
            metadata=metadata,
        )

    async def _hydrate_artifact(
        self,
        artifact: Artifact,
        *,
        ctx: ArtifactWorkingSetContext,
    ) -> MountedArtifactContent | None:
        """Hydrate artifact content without making mounts fragile."""
        try:
            return await self._hydrators.hydrate(
                artifact,
                ArtifactHydrationContext(
                    session_id=ctx.session_id,
                    workspace_id=ctx.workspace_id,
                    role=ctx.role,
                ),
            )
        except Exception:
            logger.exception(
                "Failed to hydrate artifact",
                artifact_type=artifact.type,
                artifact_id=artifact.id,
            )
            return None

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))


def build_provider() -> ArtifactWorkingSetProvider:
    """Build the tier 2 mount-only artifact working-set provider."""
    return MountOnlyArtifactWorkingSetProvider()


def _safe_path_segment(value: str) -> str:
    safe_value = _SAFE_PATH_SEGMENT_RE.sub("_", value).strip("._")
    return safe_value or "artifact"


def _prepare_host_artifact_root(host_work_dir: Path) -> Path:
    tracecat_root = host_work_dir / ".tracecat"
    _ensure_real_directory(tracecat_root)
    artifact_root = tracecat_root / "artifacts"
    _remove_path_without_following_symlink(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    return artifact_root


def _ensure_real_directory(path: Path) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, exist_ok=True)
        return

    if stat.S_ISDIR(path_stat.st_mode):
        return

    path.unlink()
    path.mkdir(parents=True, exist_ok=True)


def _remove_path_without_following_symlink(path: Path) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return

    if stat.S_ISDIR(path_stat.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _build_prompt_fragment(manifest: ArtifactWorkingSetManifest) -> str:
    artifacts = "\n".join(
        "- "
        f'artifact_id="{_prompt_string(entry.artifact_id)}" '
        f'title="{_prompt_string(entry.title)}" '
        f'path="{_prompt_string(entry.path)}"'
        for entry in manifest.artifacts
    )
    return (
        "<TracecatArtifacts>\n"
        "Workspace Chat artifacts are mounted for local inspection at "
        f"`{manifest.root}`. Read `{manifest.root}/manifest.json` for the full "
        "manifest. Each artifact entry points at hydrated content when available; "
        "`metadata.projection_path` points at the lightweight artifact projection. "
        "These files are writable scratch only: editing them does not update "
        "Tracecat, and `commit_available` is false. To persist a small semantic "
        "change, use the existing Tracecat domain tools.\n\n"
        f"{artifacts}\n"
        "</TracecatArtifacts>"
    )


def _prompt_string(value: str) -> str:
    return html.escape(value, quote=True).replace("\r", "\\r").replace("\n", "\\n")
