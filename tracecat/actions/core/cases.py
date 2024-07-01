"""Core case management actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import asyncio
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import Field

from tracecat.contexts import ctx_role, ctx_run
from tracecat.db.engine import create_vdb_conn
from tracecat.registry import registry
from tracecat.types.api import CaseContext, Suppression, Tag
from tracecat.types.cases import Case


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
    suppression: Annotated[
        list[Suppression] | None,
        Field(description="List of suppressions"),
    ] = None,
    tags: Annotated[
        list[Tag] | None,
        Field(description="List of tags"),
    ] = None,
) -> dict[str, Any]:
    db = create_vdb_conn()
    tbl = db.open_table("cases")

    run_ctx = ctx_run.get()
    role = ctx_role.get()

    if not role or not run_ctx:
        raise ValueError(f"Could not retrieve run context: {run_ctx}.")
    _context = context or []
    if isinstance(_context, dict):
        _context = [
            CaseContext(key=key, value=value) for key, value in _context.items()
        ]

    _suppression = suppression or []
    _tags = tags or []
    logger.debug(
        "Opening case",
        title=case_title,
        malice=malice,
        status=status,
        context=_context,
        suppression=_suppression,
        tags=_tags,
    )
    case = Case(
        owner_id=role.user_id,
        workflow_id=run_ctx.wf_id,
        case_title=case_title,
        payload=payload,
        malice=malice,
        status=status,
        priority=priority,
        action=action,
        context=_context,
        suppression=_suppression,
        tags=_tags,
    )
    logger.opt(lazy=True).debug("Sinking case", case=lambda: case.model_dump())
    try:
        await asyncio.to_thread(tbl.add, [case.flatten()])
    except Exception as e:
        logger.error("Failed to add case to LanceDB.", exc_info=e)
        raise
    return case.model_dump()
