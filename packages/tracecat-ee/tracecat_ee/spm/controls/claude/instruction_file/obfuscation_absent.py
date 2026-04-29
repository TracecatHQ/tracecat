from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmInstructionFileControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmInstructionFileControlData, ctx.item)
    failed = item.parse_status != "ok" or (
        item.obfuscation.get("obfuscation_detected") is True
    )
    summary = f"{item.display_name} contains obfuscation indicators"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for obfuscation analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": item.parse_status,
            "obfuscation": item.obfuscation,
        },
        recommended_payload={
            "file_path": item.file_path,
            "project_root": item.project_root,
        },
    )
