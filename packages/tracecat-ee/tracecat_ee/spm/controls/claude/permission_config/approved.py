from typing import cast

from tracecat_ee.spm.schemas import (
    SpmConfigControlData,
    SpmControlContext,
    SpmControlResult,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmConfigControlData, ctx.item)
    approved = ctx.policy.approved_permission_config
    failed = item.parse_status != "ok" or (
        approved is not None and item.value != approved
    )
    summary = f"{item.display_name} does not match the approved configuration"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for configuration analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "parse_status": item.parse_status,
            "observed": item.value,
            "approved": approved,
        },
        recommended_payload=(
            {"target_path": item.file_path, "value": approved}
            if approved is not None
            else {}
        ),
    )
