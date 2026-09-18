"""Internal semantic search transport shared by API and action gateway."""

from fastapi import APIRouter, HTTPException
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.exceptions import TracecatNotFoundError
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.schemas import SearchPage, SearchRequest
from tracecat.search.types import SearchError, SearchErrorCode

router = APIRouter()


@router.post("/{table_name}/rows/semantic-search")
async def semantic_search(
    table_name: str, params: SearchRequest, role: ExecutorWorkspaceRole
) -> SearchPage:
    """Return distinct ranked rows, bounded excerpts and an opaque continuation."""
    try:
        return await TableRetrievalService(role).search(table_name, params)
    except SearchError as exc:
        status = 404 if exc.code == SearchErrorCode.NOT_FOUND else 409
        error = HTTPException(status, detail={"code": exc.code.value})
    except EmbeddingError as exc:
        status = 422 if exc.code == EmbeddingErrorCode.INPUT_INVALID else 503
        error = HTTPException(status, detail={"code": exc.code.value})
    except TracecatNotFoundError:
        error = HTTPException(404, detail={"code": "NOT_FOUND"})
    except TracecatQueryTimeoutError:
        error = HTTPException(503, detail={"code": "TIMEOUT"})
    except (RedisError, SQLAlchemyError):
        error = HTTPException(503, detail={"code": "UNAVAILABLE"})
    raise error
