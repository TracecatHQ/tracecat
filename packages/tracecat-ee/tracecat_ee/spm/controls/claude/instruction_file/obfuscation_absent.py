from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmInstructionFileControlData, ctx.asset)
    failed = asset.parse_status != "ok" or (
        asset.obfuscation.get("obfuscation_detected") is True
    )
    summary = f"{asset.display_name} contains obfuscation indicators"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for obfuscation analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": asset.parse_status,
            "obfuscation": asset.obfuscation,
        },
        recommended_payload={
            "file_path": asset.file_path,
            "project_root": asset.project_root,
        },
    )
