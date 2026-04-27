from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmSkillControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmSkillControlData, ctx.asset)
    failed = asset.parse_status != "ok" or (
        bool(ctx.policy.approved_skills)
        and isinstance(asset.fingerprint, str)
        and asset.fingerprint not in ctx.policy.approved_skills
    )
    summary = f"{asset.display_name} is not approved"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for approval analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "fingerprint": asset.fingerprint,
            "parse_status": asset.parse_status,
        },
        recommended_payload={
            "fingerprint": asset.fingerprint,
            "target_path": asset.file_path,
        },
    )
