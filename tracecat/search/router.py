"""Internal semantic search transport shared by API and action gateway."""

from json import JSONDecodeError

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.exceptions import TracecatNotFoundError
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.search.embeddings.schemas import EmbeddingErrorRead
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.schemas import SearchPage, SearchRequest
from tracecat.search.types import SearchError, SearchErrorCode

router = APIRouter()


async def validate_query_encoding(request: Request) -> None:
    """Reject malformed Unicode before validation errors can echo invalid UTF-8."""
    try:
        body = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return  # Leave non-JSON body validation to FastAPI.
    match body:
        case {"query": str(query)} if any(0xD800 <= ord(c) <= 0xDFFF for c in query):
            raise HTTPException(
                422,
                detail=EmbeddingErrorRead(
                    code=EmbeddingErrorCode.INPUT_INVALID,
                    retryable=False,
                    retry_after=None,
                ).model_dump(),
            )


@router.post(
    "/{table_name}/rows/semantic-search",
    dependencies=[Depends(validate_query_encoding)],
)
async def semantic_search(
    table_name: str, params: SearchRequest, role: ExecutorWorkspaceRole
) -> SearchPage:
    """Return distinct ranked rows, bounded excerpts and an opaque continuation."""
    try:
        return await TableRetrievalService(role).search(table_name, params)
    except SearchError as exc:
        match exc.code:
            case SearchErrorCode.INVALID_TABLE_NAME:
                status = 400
            case SearchErrorCode.NOT_FOUND:
                status = 404
            case _:
                status = 409
        error = HTTPException(status, detail={"code": exc.code.value})
    except EmbeddingError as exc:
        match exc.code:
            case EmbeddingErrorCode.INPUT_INVALID:
                status = 422
            case EmbeddingErrorCode.CONFIGURATION_CHANGED:
                status = 409
            case EmbeddingErrorCode.RATE_LIMITED:
                status = 429
            case EmbeddingErrorCode.TIMEOUT:
                status = 504
            case EmbeddingErrorCode.UNAVAILABLE | EmbeddingErrorCode.RESPONSE_INVALID:
                status = 502
            case _:
                status = 400
        error = HTTPException(
            status,
            detail=EmbeddingErrorRead(
                code=exc.code,
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            ).model_dump(),
        )
    except TracecatNotFoundError:
        error = HTTPException(404, detail={"code": "NOT_FOUND"})
    except TracecatQueryTimeoutError:
        error = HTTPException(503, detail={"code": "TIMEOUT"})
    except (RedisError, SQLAlchemyError):
        error = HTTPException(503, detail={"code": "UNAVAILABLE"})
    raise error
