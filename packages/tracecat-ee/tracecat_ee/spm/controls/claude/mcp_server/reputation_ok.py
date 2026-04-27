from typing import Any, cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmMcpServerControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmMcpServerControlData, ctx.asset)
    reputation_status = _status("reputation_status", asset, ctx.intelligence)

    return SpmControlResult(
        failed=reputation_status == "bad",
        summary=f"{asset.display_name} has a failing reputation result",
        evidence={
            "reputation_status": reputation_status,
            "resolved_identity": asset.resolved_identity,
        },
        recommended_payload={
            "server_name": asset.server_name,
            "resolved_identity": asset.resolved_identity,
            "source_path": asset.file_path,
            "project_root": asset.project_root,
        },
        enrichment=ctx.intelligence,
    )


def _status(
    field: str,
    asset: SpmMcpServerControlData,
    intelligence: dict[str, Any],
) -> str | None:
    for source in (asset.metadata, asset.evidence, intelligence):
        value = source.get(field)
        if isinstance(value, str):
            return value
    return None
