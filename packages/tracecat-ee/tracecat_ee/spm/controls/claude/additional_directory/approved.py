from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmDirectoryControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmDirectoryControlData, ctx.item)
    failed = item.parse_status != "ok" or (
        bool(ctx.policy.approved_additional_directories)
        and item.directory_path not in ctx.policy.approved_additional_directories
    )
    summary = f"{item.display_name} is not approved"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for approval evaluation"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "directory_path": item.directory_path,
            "parse_status": item.parse_status,
        },
        recommended_payload={"directory_path": item.directory_path},
    )
