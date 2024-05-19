from fastapi import APIRouter

from tracecat.logging import logger

router = APIRouter(prefix="/test")


@router.get("/items/{id}")
async def items(
    id: str,
):
    logger.info("Got item", id=id)
    return {"status": "ok"}
