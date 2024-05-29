"""Core case management actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import asyncio
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import Field

from tracecat.contexts import ctx_role
from tracecat.db.engine import create_vdb_conn
from tracecat.db.schemas import CaseContext
from tracecat.registry import registry
from tracecat.types.api import Suppression, Tag
from tracecat.types.cases import Case


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Open a new case in the case management system.",
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
        list[CaseContext] | None,
        Field(description="List of case contexts"),
    ],
    suppression: Annotated[
        list[Suppression],
        Field(description="List of suppressions"),
    ] = None,
    tags: Annotated[
        list[Tag],
        Field(description="List of tags"),
    ] = None,
) -> dict[str, Any]:
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    role = ctx_role.get()
    if role.user_id is None:
        raise ValueError(f"User ID not found in session context: {role}.")
    # TODO: Get ar-id from temporalio?
    case = Case(
        id="PLACEHOLDER",
        owner_id=role.user_id,
        workflow_id="PLACEHOLDER",
        case_title=case_title,
        payload=payload,
        malice=malice,
        status=status,
        priority=priority,
        context=context,
        action=action,
        suppression=suppression,
        tags=tags,
    )
    logger.opt(lazy=True).debug("Sinking case", case=lambda: case.model_dump())
    try:
        await asyncio.to_thread(tbl.add, [case.flatten()])
    except Exception as e:
        logger.error("Failed to add case to LanceDB.", exc_info=e)
        raise
    return case.model_dump()
