from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmMcpServerControlData,
)


def check(ctx: SpmControlContext) -> SpmControlResult:
    item = cast(SpmMcpServerControlData, ctx.item)
    failed = item.parse_status != "ok" or (
        bool(ctx.policy.approved_mcp_servers)
        and isinstance(item.mcp_identity_key, str)
        and item.mcp_identity_key not in ctx.policy.approved_mcp_servers
    )
    summary = f"{item.display_name} is not approved"
    if item.parse_status != "ok":
        summary = f"{item.display_name} could not be parsed for approval analysis"

    return SpmControlResult(
        failed=failed,
        summary=summary,
        evidence={
            "approval_identity": item.mcp_identity_key,
            "parse_status": item.parse_status,
        },
        recommended_payload={
            "server_name": item.server_name,
            "resolved_identity": item.resolved_identity,
            "source_path": item.file_path,
            "project_root": item.project_root,
        },
    )
