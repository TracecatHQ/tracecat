from typing import Any, cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmInstructionFileControlData, ctx.item)
    reputation_status = _status(item, ctx.intelligence)

    return SpmControlResult(
        failed=reputation_status == "bad",
        summary=f"{item.display_name} contains indicators with failing reputation",
        evidence={
            "urls": item.urls,
            "domains": item.domains,
            "ips": item.ips,
            "indicator_reputation_status": reputation_status,
            "bad_indicators": ctx.intelligence.get("bad_indicators", []),
        },
        recommended_payload={
            "file_path": item.file_path,
            "project_root": item.project_root,
        },
        enrichment=ctx.intelligence,
    )


def _status(
    item: SpmInstructionFileControlData,
    intelligence: dict[str, Any],
) -> str | None:
    for source in (item.metadata, item.evidence, intelligence):
        value = source.get("reputation_status")
        if isinstance(value, str):
            return value
        value = source.get("indicator_reputation_status")
        if isinstance(value, str):
            return value
    return None
