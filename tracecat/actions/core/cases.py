"""Core case management actions."""

from typing import Annotated, Any, Literal

from pydantic import Field

from tracecat.cases.models import CaseContext, CaseCreate, Tag
from tracecat.cases.service import CaseManagementService
from tracecat.contexts import ctx_role, ctx_run
from tracecat.registry import registry


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Open a new case in the case management system.",
    default_title="Open Case",
)
async def open_case(
    # Action Inputs
    case_title: Annotated[
        str,
        Field(description="Title of the case"),
    ],
    payload: Annotated[
        dict[str, Any],
        Field(description="Payload of the case"),
    ],
    malice: Annotated[
        Literal["malicious", "benign"],
        Field(description="Malice type"),
    ],
    status: Annotated[
        Literal["open", "closed", "in_progress", "reported", "escalated"],
        Field(description="Status of the case"),
    ],
    priority: Annotated[
        Literal["low", "medium", "high", "critical"],
        Field(description="Priority of the case"),
    ],
    action: Annotated[
        Literal[
            "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
        ],
        Field(description="Action to be taken"),
    ],
    context: Annotated[
        list[CaseContext] | dict[str, Any] | None,
        Field(description="List of case contexts"),
    ] = None,
    tags: Annotated[
        list[Tag] | None,
        Field(description="List of tags"),
    ] = None,
) -> dict[str, Any]:
    """Open a new case in the case management system."""
    run = ctx_run.get()
    role = ctx_role.get()
    tags = tags or []
    context = context or []
    if isinstance(context, dict):
        context = [CaseContext(key=key, value=value) for key, value in context.items()]
    async with CaseManagementService.with_session(role=role) as service:
        params = CaseCreate(
            owner_id=role.workspace_id,
            workflow_id=run.wf_id,
            case_title=case_title,
            payload=payload,
            malice=malice,
            status=status,
            priority=priority,
            action=action,
            context=context,
            tags=tags,
        )
        _case = await service.create_case(params)

    return _case.model_dump()
