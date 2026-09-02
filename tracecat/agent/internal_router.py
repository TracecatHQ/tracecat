"""Retired executor-facing agent routes.

Registries pinned before the pydantic-ai runtime was removed still call these
paths; answer with a typed 410 instead of a bare 404.
"""

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"])

RETIRED_PATHS = ("/run", "/rank", "/rank-pairwise")


async def _retired(request: Request) -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "agent_route_retired",
            "message": (
                f"{request.url.path} was removed with the pydantic-ai runtime. "
                "Republish the workflow with `ai.agent`."
            ),
        },
    )


for _path in RETIRED_PATHS:
    router.add_api_route(_path, _retired, methods=["POST"])
