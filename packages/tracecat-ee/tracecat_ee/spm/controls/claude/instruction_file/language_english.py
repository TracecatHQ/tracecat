from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmInstructionFileControlData, ctx.asset)
    failed = asset.parse_status != "ok" or (
        asset.language_signal.get("likely_english") is False
    )
    summary = f"{asset.display_name} is not English-language"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for language analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": asset.parse_status,
            "language_signal": asset.language_signal,
        },
        recommended_payload={
            "file_path": asset.file_path,
            "project_root": asset.project_root,
        },
    )
