from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmMcpServerControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmMcpServerControlData, ctx.asset)
    failed = asset.parse_status != "ok" or (
        bool(ctx.policy.approved_mcp_servers)
        and isinstance(asset.mcp_identity_key, str)
        and asset.mcp_identity_key not in ctx.policy.approved_mcp_servers
    )
    summary = f"{asset.display_name} is not approved"
    if asset.parse_status != "ok":
        summary = f"{asset.display_name} could not be parsed for approval analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "approval_identity": asset.mcp_identity_key,
            "parse_status": asset.parse_status,
        },
        recommended_payload={
            "server_name": asset.server_name,
            "resolved_identity": asset.resolved_identity,
            "source_path": asset.file_path,
            "project_root": asset.project_root,
        },
    )
