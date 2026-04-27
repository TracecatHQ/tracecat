from typing import Any, cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmInstructionFileControlData, ctx.asset)
    reputation_status = _status(asset, ctx.intelligence)

    return SpmControlResult(
        failed=reputation_status == "bad",
        summary=f"{asset.display_name} contains indicators with failing reputation",
        evidence={
            "urls": asset.urls,
            "domains": asset.domains,
            "ips": asset.ips,
            "indicator_reputation_status": reputation_status,
            "bad_indicators": ctx.intelligence.get("bad_indicators", []),
        },
        recommended_payload={
            "file_path": asset.file_path,
            "project_root": asset.project_root,
        },
        enrichment=ctx.intelligence,
    )


def _status(
    asset: SpmInstructionFileControlData,
    intelligence: dict[str, Any],
) -> str | None:
    for source in (asset.metadata, asset.evidence, intelligence):
        value = source.get("reputation_status")
        if isinstance(value, str):
            return value
        value = source.get("indicator_reputation_status")
        if isinstance(value, str):
            return value
    return None
