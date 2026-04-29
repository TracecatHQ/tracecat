from typing import Any, cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmMcpServerControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmMcpServerControlData, ctx.item)
    reputation_status = _status("reputation_status", item, ctx.intelligence)

    return SpmControlResult(
        failed=reputation_status == "bad",
        summary=f"{item.display_name} has a failing reputation result",
        evidence={
            "reputation_status": reputation_status,
            "resolved_identity": item.resolved_identity,
        },
        recommended_payload={
            "server_name": item.server_name,
            "resolved_identity": item.resolved_identity,
            "source_path": item.file_path,
            "project_root": item.project_root,
        },
        enrichment=ctx.intelligence,
    )


def _status(
    field: str,
    item: SpmMcpServerControlData,
    intelligence: dict[str, Any],
) -> str | None:
    for source in (item.metadata, item.evidence, intelligence):
        value = source.get(field)
        if isinstance(value, str):
            return value
    return None
