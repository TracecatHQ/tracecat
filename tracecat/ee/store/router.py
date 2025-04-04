from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.ee.store.object_store import ObjectStore
from tracecat.logger import logger

router = APIRouter(prefix="/objects", tags=["objects"])


@router.get("/{object_digest}/download", tags=["objects"])
async def download_object(
    role: WorkspaceUserRole,
    object_digest: str,
    namespace: str = "default",
    expires_in_seconds: int = 3600,
):
    """
    Generate a presigned URL for downloading an object and redirect to it.

    The presigned URL will be valid for the specified duration (default: 1 hour).
    """
    logger.info(
        "Generating download URL for object",
        digest=object_digest,
        namespace=namespace,
        expires_in=expires_in_seconds,
    )

    # Create an ObjectRef from the digest and namespace
    store = ObjectStore.get()
    key = store.make_key(namespace=namespace, digest=object_digest)

    try:
        # Generate the presigned URL
        presigned_url = await store.generate_presigned_download_url(
            key=key, expires_in_seconds=expires_in_seconds
        )

        # Redirect to the presigned URL for direct download
        return RedirectResponse(url=presigned_url)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Failed to generate presigned URL", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate download URL: {str(e)}",
        ) from e
