from typing import cast

from tracecat_ee.spm.schemas import (
    SpmConfigControlData,
    SpmControlContext,
    SpmControlResult,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmConfigControlData, ctx.asset)
    approved = ctx.policy.approved_sandbox_config
    failed = asset.parse_status != "ok" or (
        approved is not None and asset.value != approved
    )
    summary = f"{asset.display_name} does not match the approved configuration"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for configuration analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": asset.parse_status,
            "observed": asset.value,
            "approved": approved,
        },
        recommended_payload=(
            {"target_path": asset.file_path, "value": approved}
            if approved is not None
            else {}
        ),
    )
