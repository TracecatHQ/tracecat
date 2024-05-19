from pathlib import Path
from typing import Any

from fastapi import APIRouter

from tracecat.auth import Role
from tracecat.contexts import ctx_role
from tracecat.experimental.dsl.dispatcher import dispatch_wofklow
from tracecat.logging import logger

router = APIRouter()


@router.post("/webhooks/{path}")
async def webhook(path: str, payload: dict[str, Any]):
    logger.info("Webhook payload", path=path, payload=payload)
    role = Role(type="service", service_id="tracecat-runner", user_id="unknown")
    ctx_role.set(role)
    with Path(f"/app/tracecat/static/workflows/{path}.yaml").resolve().open() as f:
        dsl_yaml = f.read()

    logger.info("DSL YAML", dsl_yaml=dsl_yaml)

    await dispatch_wofklow(dsl_yaml)
    return {"status": "ok"}
