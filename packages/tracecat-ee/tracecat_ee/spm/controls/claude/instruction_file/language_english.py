from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmInstructionFileControlData, ctx.item)
    failed = item.parse_status != "ok" or (
        item.language_signal.get("likely_english") is False
    )
    summary = f"{item.display_name} is not English-language"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for language analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": item.parse_status,
            "language_signal": item.language_signal,
        },
        recommended_payload={
            "file_path": item.file_path,
            "project_root": item.project_root,
        },
    )
