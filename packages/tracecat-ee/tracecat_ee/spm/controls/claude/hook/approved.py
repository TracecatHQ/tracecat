from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmHookControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmHookControlData, ctx.item)
    failed = item.parse_status != "ok" or (
        bool(ctx.policy.approved_hooks)
        and isinstance(item.fingerprint, str)
        and item.fingerprint not in ctx.policy.approved_hooks
    )
    summary = f"{item.display_name} is not approved"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for approval analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "fingerprint": item.fingerprint,
            "parse_status": item.parse_status,
        },
        recommended_payload={
            "fingerprint": item.fingerprint,
            "target_path": item.file_path,
        },
    )
