from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmDirectoryControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmDirectoryControlData, ctx.asset)
    failed = asset.parse_status != "ok" or (
        bool(ctx.policy.approved_additional_directories)
        and asset.directory_path not in ctx.policy.approved_additional_directories
    )
    summary = f"{asset.display_name} is not approved"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for approval evaluation"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "directory_path": asset.directory_path,
            "parse_status": asset.parse_status,
        },
        recommended_payload={"directory_path": asset.directory_path},
    )
